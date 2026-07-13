# AI Operator Guide

This document is written for AI coding assistants, automation agents, and contributors who want a deterministic way to deploy, run, verify, and maintain the project.

## Purpose

Use this guide when an AI tool needs to:

- bootstrap the project from a fresh checkout
- configure local development environments
- start backend, frontend, and PostgreSQL services
- validate whether the app is healthy
- run tests before a commit or deployment
- avoid damaging local secrets or tracked files

## Repository Facts

- Backend root: `backend/`
- Frontend root: `frontend/`
- Default local database: `backend/data/travel.db`
- Optional production-style database: PostgreSQL via root `compose.yaml`
- Backend dev URL: `http://127.0.0.1:8000`
- Frontend dev URL: `http://127.0.0.1:5173`
- API docs: `http://127.0.0.1:8000/docs`

## Operating Rules For AI Tools

1. Never print or commit real secrets from `.env` files.
2. Never overwrite user changes without checking `git status` first.
3. Prefer PostgreSQL for multi-user verification and SQLite for fast local smoke tests.
4. After changing backend models or persistence logic, run Alembic migration checks and backend tests.
5. After changing frontend views or services, run `npm run build` before claiming success.
6. If the task involves push notifications, remember that production delivery requires HTTPS even when localhost testing works.
7. If the task involves email, use SMTP authorization codes rather than mailbox login passwords.

## Minimal Local Bootstrap

### Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
Copy-Item .env.example .env
```

Required backend variables:

```env
AMAP_API_KEY=your_amap_web_service_key
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL_ID=your_model
AUTH_SECRET_KEY=replace_with_a_random_secret
```

Initialize and start:

```bash
python -m alembic upgrade head
python -m uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend

```bash
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

Required frontend variables:

```env
VITE_API_BASE_URL=
VITE_AMAP_WEB_JS_KEY=your_amap_js_api_key
VITE_AMAP_SECURITY_JS_CODE=your_amap_security_code
```

## PostgreSQL Flow

Use this when the task touches authenticated history, email quotas, push subscriptions, or other persistence features that should be verified in a production-like database.

```bash
Copy-Item .env.example .env
docker compose up -d postgres
cd backend
python -m alembic upgrade head
```

Set `DATABASE_URL` in `backend/.env` to the PostgreSQL instance.

## Verification Checklist

### Fast smoke check

1. `GET /health` returns `200`
2. Frontend loads without Vite import errors
3. Trip planning page opens and can submit a request
4. Result page renders without losing the generated plan

### Backend verification

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q app scripts migrations
```

### Frontend verification

```bash
cd frontend
npm run build
```

### Optional feature verification

Email:

- confirm `SEND_REAL_EMAILS=true`
- confirm SMTP host, port, username, password, and sender are present
- send to a controlled mailbox first
- verify that SMTP failures do not break trip persistence

Web Push:

- confirm `WEB_PUSH_VAPID_PUBLIC_KEY` and `WEB_PUSH_VAPID_PRIVATE_KEY` are set
- confirm the browser granted notification permission
- subscribe while logged in
- generate a trip and verify notification delivery

## Safe Git Workflow For Agents

Run in this order:

```bash
git status --short --branch
git diff --check
```

Before pushing:

```bash
cd backend
python -m pytest -q
cd ..\frontend
npm run build
```

Then commit only intended files and push.

## What To Document In Pull Requests

AI tools should summarize:

- what changed
- what was verified locally
- whether SQLite, PostgreSQL, email, or push flows were exercised
- any remaining manual deployment step
- whether secrets must be rotated after testing

## When To Read Architecture Instead

Read [architecture.md](./architecture.md) when the task is about design, data flow, trust boundaries, maps, email delivery, Web Push, or role-based access behavior.