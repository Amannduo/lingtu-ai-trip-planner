# Lingtu AI Trip Planner

[简体中文](./README.zh-CN.md) | [English](./README.en.md)

Lingtu is an open-source travel planning application for real-world trip scenarios. It combines FastAPI, Vue 3, multi-agent orchestration, and AMap services to generate multi-day itineraries with attractions, routes, weather, dining, hotels, budget estimates, printable posters, email delivery, and push notifications.

The current version requires an authenticated account for AI destination recommendations and itinerary generation. Generated plans are stored in that account's private history.

## Interface Preview

![Lingtu AI Trip Planner home page](./docs/images/home-desktop.png)

## Features

- Generate multi-day itineraries from destination, date range, budget, transport, lodging, and interests
- Validate attraction coordinates and routes with AMap POI and routing APIs
- Recommend hotel areas based on attraction clusters, cost, rating, and lodging preferences
- Export printable PDF or image results, including a trip map poster with nearby places
- Persist authenticated trip history and recover short-term local drafts
- Send finished plans by email and notify users through Web Push
- Support both SQLite for local setup and PostgreSQL for production deployment

## Documentation

- [简体中文说明](./README.zh-CN.md)
- [AI Operator Guide](./docs/ai-operator-guide.md)
- [Architecture](./docs/architecture.md)
- [Production deployment](./docs/deployment.md)
- [Volcengine Web Travel Guide Agent](./docs/volcengine-web-travel-guide-agent.md)

## Quick Start

### Requirements

- Python 3.10+
- Node.js 18+
- npm 9+
- AMap Web Service key and AMap JS key
- An OpenAI Chat Completions compatible LLM provider

### Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
Copy-Item .env.example .env
python -m alembic upgrade head
python -m uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8000
```

Required backend settings:

```env
AMAP_API_KEY=your_amap_web_service_key
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL_ID=your_model
AUTH_SECRET_KEY=replace_with_a_random_secret
```

### Frontend

```bash
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

Required frontend settings:

```env
VITE_API_BASE_URL=
VITE_AMAP_WEB_JS_KEY=your_amap_js_api_key
VITE_AMAP_SECURITY_JS_CODE=your_amap_security_code
```

Endpoints:

- Frontend: `http://127.0.0.1:5173`
- OpenAPI: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

## Deployment Notes

- Use SQLite for first-run local evaluation and contributor setup
- Use PostgreSQL for long-running, multi-user, or production environments
- Keep JWT, SMTP, VAPID, and database credentials in secrets, not in Git
- Enable HTTPS in production, especially for cookies and Web Push

## Authentication

- Passwords are stored as Argon2 hashes
- Login state is stored in `HttpOnly` cookies
- AI recommendations, generation jobs, itinerary generation, and history endpoints require authentication
- Backend authorization is derived from the server-side session, not from frontend role fields
- Higher-privilege roles are meant for self-hosted operational scenarios and should be protected through server-side invite configuration

## Email and Push

- SMTP delivery can send completed plans to a bound account email or a per-request recipient address
- QQ Mail requires an SMTP authorization code instead of the account login password
- Web Push uses Service Worker, VAPID, authenticated subscriptions, retry limits, and public-IP endpoint validation

## Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q app scripts migrations

cd ..\frontend
npm run build
```

## Repository Layout

```text
backend/   FastAPI app, migrations, scripts, tests
frontend/  Vue app, services, components, views
docs/      operator and architecture documentation
```

## License

Released under the [MIT License](./LICENSE).
