# Lingtu AI Trip Planner

[简体中文](./README.zh-CN.md) | [English](./README.en.md)

Lingtu is an open-source trip planning application for real travel scenarios. It combines FastAPI, Vue 3, multi-agent planning workflows, and AMap data to generate multi-day travel plans with attractions, routes, weather, dining, hotels, budget estimates, printable map posters, email delivery, and browser push notifications.

## Highlights

- Multi-day itinerary generation from destination, date range, budget, transport, lodging, and preferences
- Attraction coordinate validation and route rendering backed by AMap POI and routing services
- Printable trip poster and PDF/image export optimized for clarity and file size
- Authenticated trip history, editable results, and short-term local draft recovery
- Email delivery for finished plans and authenticated Web Push notifications
- SQLite for quick local setup and PostgreSQL for long-running multi-user deployment

## Documentation

- [中文说明](./README.zh-CN.md)
- [English documentation](./README.en.md)
- [AI Operator Guide](./docs/ai-operator-guide.md)
- [Architecture](./docs/architecture.md)
- [Volcengine Web Travel Guide Agent](./docs/volcengine-web-travel-guide-agent.md)

## Quick Start

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

Required backend environment values:

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

Required frontend environment values:

```env
VITE_API_BASE_URL=
VITE_AMAP_WEB_JS_KEY=your_amap_js_api_key
VITE_AMAP_SECURITY_JS_CODE=your_amap_security_code
```

Visit:

- Frontend: `http://127.0.0.1:5173`
- OpenAPI: `http://127.0.0.1:8000/docs`
- Health: `http://127.0.0.1:8000/health`

## Tech Stack

- Frontend: Vue 3, TypeScript, Vite, Ant Design Vue, ECharts
- Backend: FastAPI, Pydantic, LangGraph
- Database: SQLite, PostgreSQL, SQLAlchemy, Alembic
- Maps: AMap Web Service API, AMap JS API 2.0
- Delivery: SMTP, Web Push, Service Worker, VAPID

## License

Released under the [MIT License](./LICENSE).