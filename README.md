# AI Academic TA & Study Assistant

A Flask web application for Socratic computer-science tutoring, chat history, PDF reference uploads, and AI-generated study guides. Conversations and study guides are generated with Google's Gemini API.

## Features

- User registration, login, and user-owned chat sessions.
- Persistent messages and session history using SQLAlchemy.
- PDF reference uploads for Gemini-assisted tutoring (maximum upload size: 16 MB).
- Chat summaries formatted as study guides, with a readable fallback if summary generation is unavailable.
- Retried Gemini generation requests with exponential backoff and a newly initialized client per attempt.
- Markdown rendering in the browser, sanitized with DOMPurify.

## Stack

- Python 3.10+
- Flask, Flask-Login, Flask-SQLAlchemy, and Werkzeug
- `google-genai` with the `gemini-3.5-flash-lite` model
- SQLite by default; PostgreSQL supported via `DATABASE_URL`
- Jinja templates, Tailwind CSS CDN, Marked, and DOMPurify

## Requirements

- Python 3.10 or newer
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/)
- PostgreSQL only if choosing PostgreSQL instead of SQLite

## Local setup

Run these commands from the `ai-ta-chatbot` project directory.

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks environment activation, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` in that terminal and activate the environment again.

### macOS or Linux

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set `GEMINI_API_KEY` to your key. Set `SECRET_KEY` to a long, random value; the development default is not suitable for deployment. Never commit `.env`, API keys, database credentials, or other secrets.

`DATABASE_URL` is optional. Without it, the application uses a local SQLite database. For PostgreSQL, set a SQLAlchemy URL such as `postgresql://user:password@host:5432/database`. Keep the credentials in environment configuration, not in source files.

## Run

With the virtual environment activated and `.env` configured:

```sh
python app.py
```

Open <http://127.0.0.1:5000>. The application creates its database tables at startup. Flask's built-in server is for local development only; deploy behind a production WSGI server and HTTPS.

## Gemini connection troubleshooting

The app uses a 60-second Gemini request timeout and configures the HTTPX transport to use IPv4 with connection retries. If Gemini requests still fail with TLS or connection errors, check that the current network, firewall, VPN, or proxy permits HTTPS access to Google's API. Do not disable TLS certificate verification. A summary fallback keeps the summary endpoint usable when Gemini remains unavailable; chat generation reports a connection error instead.

## Project layout

```text
ai-ta-chatbot/
├── app.py                 # Flask routes, Gemini integration, and app setup
├── models.py              # User, chat, message, and reference-file models
├── requirements.txt       # Python dependencies
├── templates/             # Jinja templates and chat UI
├── uploads/               # Temporary local staging for uploaded PDFs
├── .env.example           # Safe configuration template
├── .gitignore             # Excludes secrets and local/generated files
└── README.md
```

## Before publishing or deploying

- Check `git status` and confirm no `.env` file, API key, database, uploaded PDF, virtual environment, or generated media is staged.
- If a secret was ever committed, remove it from Git tracking/history as appropriate and revoke or rotate it. Adding it to `.gitignore` does not remove an already-tracked file.
- Configure a unique `SECRET_KEY`, HTTPS, secure cookie settings, upload limits, and a production WSGI server before public deployment.
- Treat uploaded documents and AI responses as untrusted input. Review dependencies and deployment settings regularly.

## Possible next steps

Automated tests, database migrations, rate limiting, managed file storage, upload lifecycle cleanup, and study-guide export are not currently included.
