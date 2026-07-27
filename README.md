# 灵途 AI 旅行助手

[![CI](https://github.com/Amannduo/lingtu-ai-trip-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/Amannduo/lingtu-ai-trip-planner/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-0f766e.svg)](./LICENSE)

[简体中文](./README.zh-CN.md) · [English](./README.en.md) · [架构说明](./docs/architecture.md) · [部署指南](./docs/deployment.md)

灵途是一个面向真实旅行场景的开源 AI 行程规划应用。用户只需用自然语言描述出发地、时间、人数、预算和旅行偏好，系统就会结合多智能体工作流、高德地图数据与质量检查，推荐目的地并生成可查看、编辑、保存和导出的多日旅行计划。

## 界面预览

![灵途 AI 旅行助手首页](./docs/images/home-desktop.png)

## 它能做什么

- **自然语言选目的地**：不知道去哪也可以先描述旅行愿望，AI 会给出多个可比较方向。
- **生成可执行行程**：整理每日景点、路线、天气、餐饮、住宿和预算，而不只是输出一段攻略。
- **地图与事实辅助**：使用高德 POI、坐标和路线服务校验地点与通行路径。
- **行前提醒**：将系统检查结果转换为面向旅行者的门票、住宿、节奏和信息时效提醒。
- **账号与历史计划**：登录后自动保存生成结果，只允许当前账号读取和编辑自己的计划。
- **分享与导出**：支持长图、PDF、旅行地图手册、邮件投递和浏览器通知。

## 使用流程

```text
登录 / 注册
  → 用一句话描述旅行需求
  → 对比 AI 推荐的目的地
  → 确认日期、人数、预算与偏好
  → 生成并检查多日行程
  → 在历史计划中继续查看、编辑或导出
```

> 当前版本要求登录后使用 AI 推荐与行程生成功能。认证和数据权限由后端强制执行，前端字段不能绕过权限检查。

## 技术架构

| 层 | 技术 |
| --- | --- |
| Web 前端 | Vue 3、TypeScript、Vite、Ant Design Vue、ECharts |
| API 后端 | FastAPI、Pydantic、LangGraph |
| 数据存储 | SQLite、PostgreSQL、SQLAlchemy、Alembic |
| 地图能力 | 高德地图 Web 服务 API、JS API 2.0 |
| 账号安全 | Argon2、PyJWT、HttpOnly Cookie |
| 导出与通知 | jsPDF、html2canvas、SMTP、Web Push |

```text
frontend/  ──HTTP / SSE──>  backend/
   Vue                       FastAPI
    │                           │
    ├─ 结果展示与导出            ├─ 多智能体行程规划
    ├─ 高德交互地图              ├─ 地图 / 路线 / 质量检查
    └─ 登录与历史入口            └─ PostgreSQL / SQLite
```

## 本地快速启动

### 环境要求

- Python 3.10+
- Node.js 18+、npm 9+
- 高德地图 Web 服务 Key 与 JS API Key
- OpenAI Chat Completions 兼容的模型服务

### 1. 启动后端

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

复制 `backend/.env.example` 为 `backend/.env`，至少配置：

```env
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL_ID=your_model
AMAP_API_KEY=your_amap_web_service_key
AUTH_SECRET_KEY=replace_with_a_random_secret
```

```bash
python -m alembic upgrade head
python -m uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8000
```

### 2. 启动前端

```bash
cd frontend
npm ci
# 复制 .env.example 为 .env
npm run dev
```

在 `frontend/.env` 中配置：

```env
VITE_API_BASE_URL=
VITE_AMAP_WEB_JS_KEY=your_amap_js_api_key
VITE_AMAP_SECURITY_JS_CODE=your_amap_security_code
```

本地地址：

- 应用：`http://127.0.0.1:5173`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

## 数据库与部署

- 本地体验默认使用 SQLite：`backend/data/travel.db`
- 多用户或长期部署推荐 PostgreSQL
- 根目录 `compose.yaml` 可启动本地 PostgreSQL
- 正式环境必须配置 HTTPS、随机认证密钥、严格 CORS、数据库备份和 Secret 管理

完整步骤见 [生产部署指南](./docs/deployment.md)。

## 验证项目

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q app scripts migrations

cd ../frontend
npm ci
npm run build
```

GitHub Actions 会在提交和 Pull Request 中验证后端 SQLite/PostgreSQL 测试以及前端生产构建。

## 目录结构

```text
backend/
  app/          FastAPI、智能体、服务和数据模型
  migrations/   Alembic 数据库迁移
  tests/        后端自动化测试
frontend/
  src/          Vue 页面、组件和客户端服务
docs/           架构、部署与运维文档
compose.yaml    本地 PostgreSQL 配置
```

## 使用说明

AI 生成内容可能受模型、地图数据和实时信息变化影响。门票、开放时间、酒店价格、天气与交通信息应在出发前通过官方渠道再次确认。

## License

本项目采用 [MIT License](./LICENSE)。
