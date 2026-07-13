# Architecture

This document explains how the project works internally. It is the design and implementation reference, not the end-user operating guide.

## Scope

Read this document when you need to understand:

- how itinerary generation flows through backend agents and services
- how authentication, history, and persistence are enforced
- how map coordinates, routes, and poster rendering are produced
- how email delivery and Web Push are triggered after a plan is saved
- why SQLite and PostgreSQL are both supported

For deployment and task execution steps, read [AI Operator Guide](./ai-operator-guide.md).

## High-Level Architecture

The repository is split into two main applications:

- `backend/`: FastAPI application, AI orchestration, persistence, authentication, email, push, migrations, tests
- `frontend/`: Vue application for trip creation, result viewing, history, export, and browser notifications

The system boundary is straightforward:

1. the frontend collects trip preferences and sends them to the backend
2. the backend gathers map, weather, hotel, and model-driven planning data
3. the backend returns a normalized trip plan and optionally persists it
4. the frontend renders the plan, poster, exports, and push subscription state

## Backend Modules

### API routes

Main route groups live in `backend/app/api/routes/`:

- `trip.py`: trip generation, history, detail update
- `auth.py`: register, login, logout, account profile
- `map.py`: context information used by the trip map poster
- `push.py`: authenticated Web Push subscription management
- `agent.py`: analysis and document-related AI capabilities

### Agents and planning

Core planning logic is centered around:

- `trip_planner_agent.py`
- `agents/graph/travel_agent_graph.py`
- `web_travel_guide_agent.py`
- `destination_recommender_agent.py`

These modules combine LLM output with structured enrichment from services. The backend is responsible for normalizing the final result shape before returning it.

### Services

Important backend services include:

- `amap_service.py`: POI lookup, coordinate normalization, route and nearby-place queries
- `auth_service.py`: password hashing, login checks, role handling, account persistence
- `database_service.py` and `db_models.py`: SQLAlchemy engine and portable schema definitions
- `travel_plan_data_service.py`: trip history and detail persistence
- `trip_email_service.py` and `send_email_tool.py`: email composition and SMTP delivery isolation
- `web_push_service.py`: VAPID-backed subscription persistence and best-effort push delivery
- `email_quota_service.py`: durable rate limits for real SMTP sends

## Frontend Modules

### Main views

- `Home.vue`: trip form, recommendations, history entry, email and push toggles
- `Result.vue`: itinerary display, edit mode, export, map rendering, local recovery

### Shared services

- `api.ts`: typed API access and response normalization
- `auth.ts`: local account state and session-related helpers
- `pushNotifications.ts`: Service Worker registration, permission state, subscribe/unsubscribe flow
- `tripCache.ts`: short-term browser persistence and recovery for anonymous or interrupted flows

### Poster rendering

`TripMapPoster.vue` is the printable map-handbook component. It is designed to provide a static but readable spatial overview, not turn-by-turn navigation. The interactive AMap panel remains the richer navigation surface during normal use.

## Persistence Model

The project deliberately supports two database modes with the same schema and Alembic history:

- SQLite for zero-friction local evaluation and contributor onboarding
- PostgreSQL for real deployments with concurrency, backup, and operational controls

This is why schema logic lives in portable SQLAlchemy models and why migrations must remain explicit and reproducible.

## Authentication Model

Authentication is server-enforced.

- passwords are stored as Argon2 hashes
- login state is kept in `HttpOnly` cookies
- protected requests reload the current user from the database
- frontend role fields are not trusted for authorization decisions
- higher-privilege roles are intended for self-hosted operational workflows and should be guarded by server-side invite configuration

## Trip Generation Flow

A typical trip request follows this path:

1. frontend submits planning form data to `/api/trip/plan`
2. backend validates request schema
3. backend queries supporting services such as map, weather, transport, hotel, and LLM planning
4. backend assembles a normalized plan object
5. if authenticated, backend persists summary and detail records
6. backend optionally sends email and Web Push as non-critical post-save side effects
7. frontend caches the plan locally and renders the result page

Anonymous generation is viewable locally but is not stored in authenticated history.

## Map and Route Integrity

The project uses AMap as the primary geographic authority.

- attractions prefer validated POI coordinates
- route segments prefer AMap-verified paths when available
- the map poster adds surrounding POIs for spatial context
- the frontend interactive map draws verified polylines when the backend provides them

The poster is optimized for readability, export, and print, while the live map remains the interactive navigation surface.

## Export Strategy

PDF and image export are generated in the frontend from rendered content.

Design constraints:

- readable print output
- stable layout across devices
- reduced file size where possible
- static map poster included even when live map canvases are unsuitable for PDF export

## Email Delivery Design

Email is optional and isolated from the core save path.

- trip persistence should succeed even if SMTP delivery fails
- SMTP configuration errors should return explicit delivery feedback
- real sends are rate-limited per authenticated user and peer IP bucket
- dry-run mode allows UI and backend flow testing without actual delivery

## Web Push Design

Web Push is authenticated and best-effort.

- subscriptions are stored per authenticated user and per browser/device
- invalid subscriptions are removed on terminal failure responses such as 404 or 410
- delivery has retry and budget limits
- endpoint validation rejects unsafe or non-public targets
- push failure must not roll back a saved trip

## Documentation Layout Recommendation

For GitHub-facing documentation, this repository should keep a clear split:

- `README.md`: landing page, highlights, quick start, doc index
- `README.zh-CN.md`: full Chinese usage and deployment documentation
- `README.en.md`: English overview and quick start
- `docs/ai-operator-guide.md`: step-by-step run, deploy, verify instructions for AI tools and operators
- `docs/architecture.md`: implementation principles, module boundaries, and design decisions

This split matches how many active open-source projects separate onboarding from internal design.