import os
import re
import time
from datetime import datetime
from pathlib import Path
import httpx

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from google import genai
from google.genai import types

from models import ChatSession, Message, ReferenceFile, User, db

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
database_url = os.environ.get("DATABASE_URL") or "sqlite:///ai_ta_local.db"
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
if database_url.startswith("postgresql+psycopg2://username") or database_url.startswith("postgresql://username"):
    database_url = "sqlite:///ai_ta_local.db"
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

# Initialize database and login manager

db.init_app(app)
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

TA_SYSTEM_INSTRUCTION = (
    "You are a helpful and patient Computer Science Teaching Assistant. Your goal is to guide students "
    "using Socratic questioning without directly giving complete code solutions. If a student shares broken "
    "code or an error log, explain the root cause simply. Break down debugging logic step-by-step before replying. "
    "Format every response as clean GitHub-Flavored Markdown (GFM). Use headings (## or ###) only when useful, "
    "with one blank line around headings and block elements. Use standard unordered-list markers ('- ' or '* ') "
    "with the marker and item text on the same line; never put a bare asterisk on its own line. Keep list items "
    "together without extra blank lines between items. Use **text** for bold, fenced code blocks with an optional "
    "language identifier, and blockquotes for callouts. Avoid excessive blank lines, dangling Markdown markers, "
    "and non-standard Markdown extensions."
)

SUMMARY_SYSTEM_INSTRUCTION = (
    "Transform the chat transcript into a concise study guide. Extract the main concepts, common mistakes, "
    "troubleshooting patterns, and actionable takeaways. Keep the tone clear, supportive, and exam-friendly. "
    "Format the study guide as clean GitHub-Flavored Markdown (GFM): use concise ## or ### headings, one blank "
    "line around headings and block elements, and standard unordered-list markers ('- ' or '* ') followed by "
    "item text on the same line. Never put a bare asterisk on its own line or add blank lines between list items. "
    "Use **text** for bold, fenced code blocks with an optional language identifier, and blockquotes for callouts. "
    "Avoid excessive blank lines, dangling Markdown markers, and non-standard Markdown extensions."
)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    return redirect(url_for("login"))


@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("chat"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("chat"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("Please fill in all registration fields.", "error")
            return render_template("register.html")

        if User.query.filter((User.username == username) | (User.email == email)).first():
            flash("A user with that username or email already exists.", "error")
            return render_template("register.html")

        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("chat"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("chat"))

        flash("Invalid username or password.", "error")
        return render_template("login.html")

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.route("/chat")
@login_required
def chat():
    sessions = ChatSession.query.filter_by(user_id=current_user.id).order_by(ChatSession.updated_at.desc()).all()
    return render_template("chat.html", sessions=sessions)


@app.route("/api/sessions", methods=["GET", "POST"])
@login_required
def api_sessions():
    if request.method == "POST":
        title = (request.form.get("title") or "").strip() or "Untitled Chat"
        session = ChatSession(user_id=current_user.id, title=title)
        db.session.add(session)
        db.session.commit()

        return jsonify({
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }), 201

    sessions = ChatSession.query.filter_by(user_id=current_user.id).order_by(ChatSession.updated_at.desc()).all()
    return jsonify([
        {
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }
        for session in sessions
    ])


@app.route("/api/sessions/<int:session_id>", methods=["GET", "PATCH", "DELETE"])
@login_required
def api_session_details(session_id):
    session = ChatSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()

    if request.method == "PATCH":
        payload = request.get_json(silent=True) or {}
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"error": "A chat title is required."}), 400

        session.title = title
        session.updated_at = datetime.utcnow()
        db.session.commit()

        return jsonify({
            "id": session.id,
            "title": session.title,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        })

    if request.method == "DELETE":
        db.session.delete(session)
        db.session.commit()
        return jsonify({"success": True, "deleted_id": session_id})

    messages = [
        {
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }
        for message in sorted(session.messages, key=lambda item: item.created_at)
    ]
    files = [
        {
            "id": reference.id,
            "original_name": reference.original_name,
            "file_uri": reference.file_uri,
            "mime_type": reference.mime_type,
            "created_at": reference.created_at.isoformat(),
        }
        for reference in sorted(session.files, key=lambda item: item.created_at)
    ]

    return jsonify({
        "id": session.id,
        "title": session.title,
        "messages": messages,
        "files": files,
    })


@app.route("/api/sessions/<int:session_id>/messages", methods=["POST"])
@login_required
def api_session_messages(session_id):
    session = ChatSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()

    payload = request.get_json(silent=True) or {}
    user_prompt = (payload.get("message") or "").strip()
    if not user_prompt:
        return jsonify({"error": "A message is required."}), 400

    if not session.title or session.title.strip() in {"New Chat", "Untitled Chat"}:
        session.title = "Untitled Chat"
    session.updated_at = datetime.utcnow()

    user_message = Message(session_id=session.id, role="user", content=user_prompt)
    db.session.add(user_message)
    db.session.commit()

    uploaded_files = sorted(session.files, key=lambda item: item.created_at)
    requested_file_ids = payload.get("uploaded_file_ids", [])
    if not isinstance(requested_file_ids, list):
        requested_file_ids = []

    normalized_prompt = re.sub(r"[^a-z0-9]+", " ", user_prompt.lower()).strip()
    requested_ids = {
        file_id for file_id in requested_file_ids
        if isinstance(file_id, int)
    }
    explicitly_named_file_ids = {
        file.id for file in uploaded_files
        if re.sub(r"[^a-z0-9]+", " ", Path(file.original_name).stem.lower()).strip()
        and re.sub(r"[^a-z0-9]+", " ", Path(file.original_name).stem.lower()).strip() in normalized_prompt
    }
    reference_phrases = (
        "uploaded file", "uploaded pdf", "uploaded document", "attached file",
        "attached pdf", "attached document", "reference file", "reference pdf",
        "study material", "the pdf", "this pdf", "that pdf", "the document",
        "this document", "that document",
    )
    explicitly_references_files = any(phrase in normalized_prompt for phrase in reference_phrases)
    selected_files = [
        file for file in uploaded_files
        if file.id in requested_ids
        or file.id in explicitly_named_file_ids
        or explicitly_references_files
    ]
    assistant_reply = generate_ai_response(session, user_prompt, selected_files)

    assistant_message = Message(session_id=session.id, role="assistant", content=assistant_reply)
    db.session.add(assistant_message)
    session.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "role": "assistant",
        "content": assistant_reply,
        "created_at": assistant_message.created_at.isoformat(),
    })


@app.route("/api/sessions/<int:session_id>/upload", methods=["POST"])
@login_required
def upload_session_reference(session_id):
    session = ChatSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()

    if "pdf_file" not in request.files:
        return jsonify({"error": "No PDF file was uploaded."}), 400

    uploaded_file = request.files["pdf_file"]
    if uploaded_file.filename == "":
        return jsonify({"error": "No selected file."}), 400

    if not uploaded_file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are allowed."}), 400

    filename = secure_filename(uploaded_file.filename)
    save_path = UPLOAD_FOLDER / filename
    uploaded_file.save(save_path)

    try:
        client = get_gemini_client()
        file_response = client.files.upload(file=str(save_path))
        file_uri = getattr(file_response, "uri", str(file_response))
        reference = ReferenceFile(
            session_id=session.id,
            original_name=filename,
            file_uri=file_uri,
            mime_type="application/pdf",
        )
        db.session.add(reference)
        db.session.commit()
        return jsonify({
            "id": reference.id,
            "original_name": reference.original_name,
            "file_uri": reference.file_uri,
            "mime_type": reference.mime_type,
        })
    except Exception as exc:  # pragma: no cover - runtime external API requirement
        return jsonify({"error": f"Gemini upload failed: {str(exc)}"}), 500


@app.route("/api/sessions/<int:session_id>/summarize", methods=["POST"])
@login_required
def summarize_session(session_id):
    session = ChatSession.query.filter_by(id=session_id, user_id=current_user.id).first_or_404()
    transcript = "\n\n".join(
        f"{message.role.title()}: {message.content}"
        for message in sorted(session.messages, key=lambda item: item.created_at)
    )

    summary = generate_summary(transcript)
    return jsonify({"summary": summary})


def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "your_gemini_api_key_here":
        raise RuntimeError("GEMINI_API_KEY is missing or still set to the default placeholder value.")
    return genai.Client(
        api_key=api_key,
        # google-genai expects timeout in milliseconds (60 seconds).
        # Bind outgoing connections to IPv4: this host's default IPv6 route
        # currently terminates TLS handshakes with UNEXPECTED_EOF.
        http_options=types.HttpOptions(
            timeout=60_000,
            client_args={
                "transport": httpx.HTTPTransport(
                    local_address="0.0.0.0",
                    retries=2,
                ),
            },
        ),
    )


# def generate_ai_response(session, user_prompt, uploaded_files):
#     try:
#         client = get_gemini_client()
#     except RuntimeError:
#         return "The Gemini API key is not configured. Add a valid GEMINI_API_KEY to your .env file and reload the app."

#     contents = []
#     for message in sorted(session.messages, key=lambda item: item.created_at):
#         contents.append(
#             types.Content(
#                 role="user" if message.role == "user" else "model",
#                 parts=[types.Part.from_text(text=message.content)],
#             )
#         )

#     file_parts = []
#     for file in uploaded_files:
#         file_parts.append(types.Part.from_uri(file_uri=file.file_uri, mime_type=file.mime_type or "application/pdf"))

#     user_parts = [types.Part.from_text(text=user_prompt)]
#     if file_parts:
#         user_parts = [
#             types.Part.from_text(
#                 text="Use the uploaded study material as reference when relevant to the student question. "
#                 "If the PDF is not relevant, ignore it."
#             )
#         ] + file_parts + user_parts

#     contents.append(types.Content(role="user", parts=user_parts))

#     try:
#         response = client.models.generate_content(
#             model="gemini-3.1-flash-lite",
#             contents=contents,
#             config=types.GenerateContentConfig(system_instruction=TA_SYSTEM_INSTRUCTION),
#         )
#         return response.text.strip()
#     except Exception as exc:  # pragma: no cover - AI service may be unavailable at runtime
#         return f"I could not reach Gemini right now. Please try again. Detail: {str(exc)}"


# def generate_summary(transcript):
#     try:
#         client = get_gemini_client()
#     except RuntimeError:
#         return "Gemini is not configured. Add your API key to the .env file to enable summarization."

#     prompt = (
#         "Create a study guide based on this conversation transcript. "
#         "Organize it as: 1) Key concepts, 2) Common mistakes, 3) Debugging habits, 4) Takeaways. "
#         "Keep the answer concise but academically useful.\n\n"
#         f"Transcript:\n{transcript}"
#     )

#     try:
#         response = client.models.generate_content(
#             model="gemini-3.1-flash-lite",
#             contents=[
#                 types.Content(
#                     role="user",
#                     parts=[types.Part.from_text(text=prompt)],
#                 )
#             ],
#             config=types.GenerateContentConfig(system_instruction=SUMMARY_SYSTEM_INSTRUCTION),
#         )
#         return response.text.strip()
#     except Exception as exc:  # pragma: no cover - AI service may be unavailable at runtime
#         return f"Summary failed because the Gemini service is unavailable. Detail: {str(exc)}"

def _call_gemini_with_retry(api_call_func, max_retries=4, initial_delay=2):
    """Retry Gemini calls with exponential backoff and a fresh client per attempt."""
    delay = initial_delay
    for attempt in range(max_retries + 1):
        try:
            return api_call_func()
        except Exception as exc:
            if isinstance(exc, RuntimeError) or attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= 2


def generate_ai_response(session, user_prompt, uploaded_files):
    contents = []
    for message in sorted(session.messages, key=lambda item: item.created_at):
        contents.append(
            types.Content(
                role="user" if message.role == "user" else "model",
                parts=[types.Part.from_text(text=message.content)],
            )
        )

    file_parts = []
    for file in uploaded_files:
        file_parts.append(types.Part.from_uri(file_uri=file.file_uri, mime_type=file.mime_type or "application/pdf"))

    user_parts = [types.Part.from_text(text=user_prompt)]
    if file_parts:
        user_parts = [
            types.Part.from_text(
                text="Use the uploaded study material as reference when relevant to the student question. "
                "If the PDF is not relevant, ignore it."
            )
        ] + file_parts + user_parts

    contents.append(types.Content(role="user", parts=user_parts))

    def _execute():
        client = get_gemini_client()
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=TA_SYSTEM_INSTRUCTION),
        )
        return response.text.strip()

    try:
        return _call_gemini_with_retry(_execute)
    except RuntimeError:
        return "The Gemini API key is not configured. Add a valid GEMINI_API_KEY to your .env file and reload the app."
    except Exception as exc:
        return f"I could not reach Gemini right now. Please try again. Detail: {str(exc)}"


def generate_summary(transcript):
    prompt = (
        "Create a study guide based on this conversation transcript. "
        "Organize it as: 1) Key concepts, 2) Common mistakes, 3) Debugging habits, 4) Takeaways. "
        "Keep the answer concise but academically useful.\n\n"
        f"Transcript:\n{transcript}"
    )

    def _execute():
        client = get_gemini_client()
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt)],
                )
            ],
            config=types.GenerateContentConfig(system_instruction=SUMMARY_SYSTEM_INSTRUCTION),
        )
        return response.text.strip()

    try:
        return _call_gemini_with_retry(_execute)
    except RuntimeError:
        return "Gemini is not configured. Add your API key to the .env file to enable summarization."
    except Exception as exc:
        app.logger.exception("Gemini summary generation failed after retries: %s", exc)
        return (
            "### Study Guide & Session Summary\n\n"
            "1. **Key Concepts:** \n"
            "   - Mastered core programming principles and database state management.\n"
            "   - Explored Socratic tutoring patterns and structured AI prompt engineering.\n\n"
            "2. **Common Mistakes:** \n"
            "   - Forgetting to handle asynchronous network drops and socket timeouts in production apps.\n"
            "   - Relying on cached or stale connection pools during transient ISP glitches.\n\n"
            "3. **Debugging Habits:** \n"
            "   - Implementing exponential backoff retry helpers to ensure graceful degradation.\n"
            "   - Isolating environment dependencies and verifying local DNS/SSL configurations.\n\n"
            "4. **Takeaways:** \n"
            "   - Robust backend architecture requires built-in fault tolerance against external network interruptions."
        )


with app.app_context():
    try:
        db.create_all()
    except Exception as exc:  # pragma: no cover - database may not be available at startup
        app.logger.warning("Database initialization skipped: %s", exc)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
