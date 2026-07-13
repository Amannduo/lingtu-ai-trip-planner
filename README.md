# 灵途 AI 旅行规划师

灵途是一个面向真实旅行场景的开源行程规划应用。它结合 FastAPI、Vue 3、多智能体工作流和高德地图数据，生成包含景点、路线、天气、餐饮、酒店与预算的多日旅行计划，并支持历史记录、地图手册、PDF 导出、邮件投递和数据分析。

## 功能

- 根据目的地、日期、人数、预算、交通、住宿和兴趣偏好生成多日行程。
- 使用高德地图 POI、GCJ-02 坐标和路线服务校验景点位置与通行路线。
- 结合景点分布、交通成本、价格、评分和住宿偏好推荐酒店区域。
- 生成可放大、下载和打印的旅行地图手册，展示景点及周边餐馆、商店和交通场所。
- 导出经过打印样式优化和压缩处理的 PDF 或长图。
- 登录后自动保存行程，支持历史记录读取、修改和短期浏览器草稿保留。
- 使用数据库账号、Argon2 密码哈希和 HttpOnly JWT Cookie 实现后端认证。
- 账号可绑定邮箱，也可为单次行程指定收件地址；生成完成后可发送完整计划。
- 支持 Service Worker + Web Push 后台通知，行程生成后可在页面关闭时提示用户。
- 多智能体分析支持用户画像、目的地统计、预算趋势、推荐、预测、图表和文件分析。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3、TypeScript、Vite、Ant Design Vue、ECharts |
| 后端 | FastAPI、Pydantic、LangGraph、HelloAgents |
| 数据库 | SQLite（默认）、PostgreSQL（生产推荐）、SQLAlchemy、Alembic |
| 地图 | 高德地图 Web 服务 API、JS API 2.0 |
| 认证 | Argon2、PyJWT、HttpOnly Cookie |
| 导出 | jsPDF、html2canvas |
| 可选服务 | SMTP、Web Push/VAPID、Unsplash、火山引擎联网问答、FlyAI |

## 数据库选择

同一套 SQLAlchemy 表结构和 Alembic 迁移同时支持 SQLite 与 PostgreSQL。

| 场景 | 推荐 |
| --- | --- |
| 首次体验、个人本地使用、贡献者开发 | SQLite |
| 多用户部署、长期运行、并发写入、备份与监控 | PostgreSQL |
| 自动化测试 | SQLite 和 PostgreSQL 都应验证 |

未配置 `DATABASE_URL` 时，后端使用 `backend/data/travel.db`。生产环境应使用独立的低权限 PostgreSQL 应用账号，不要让应用连接数据库管理员账号。

## 快速开始

### 环境要求

- Python 3.10 或更高版本
- Node.js 18 或更高版本
- npm 9 或更高版本
- 高德地图 Web 服务 Key 和 JS API Key
- 一个兼容 OpenAI Chat Completions 的 LLM 服务

### 1. 启动后端

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

复制并编辑环境文件：

```bash
# Windows PowerShell
Copy-Item .env.example .env

# macOS/Linux
# cp .env.example .env
```

至少配置以下内容：

```env
AMAP_API_KEY=your_amap_web_service_key
LLM_API_KEY=your_llm_api_key
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL_ID=your_model
AUTH_SECRET_KEY=replace_with_a_random_secret
```

生成认证密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

初始化 SQLite 并启动：

```bash
python -m alembic upgrade head
python -m uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8000
```

### 2. 启动前端

```bash
cd frontend
npm ci
# Windows PowerShell: Copy-Item .env.example .env
# macOS/Linux: cp .env.example .env
npm run dev
```

在 `frontend/.env` 中配置：

```env
VITE_API_BASE_URL=
VITE_AMAP_WEB_JS_KEY=your_amap_js_api_key
VITE_AMAP_SECURITY_JS_CODE=your_amap_security_code
```

访问：

- 前端：http://127.0.0.1:5173
- OpenAPI：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

## PostgreSQL

### Docker Compose

根目录的 Compose 文件只启动 PostgreSQL，后端和前端仍按上面的方式运行。

```bash
# Windows PowerShell
Copy-Item .env.example .env
docker compose up -d postgres
```

为根目录 `.env` 设置随机的 `POSTGRES_PASSWORD`，然后在 `backend/.env` 配置：

```env
DATABASE_URL=postgresql+psycopg://lingtu_app:your_url_encoded_password@localhost:5432/lingtu_travel
```

执行迁移：

```bash
cd backend
python -m alembic upgrade head
```

若密码包含特殊字符，需要先进行 URL 编码。部署时建议使用托管密钥或容器 Secret，而不是把密码写入镜像或提交到 Git。

## 认证与账号邮箱

- 用户、密码哈希、角色和绑定邮箱都保存在后端数据库。
- 密码使用 Argon2 哈希，服务端不保存明文密码。
- 登录令牌放在 `HttpOnly`、`SameSite=Lax` Cookie 中。
- 每次受保护请求都会从数据库重新读取用户身份与权限。
- 前端提交的 `user_id` 或 `role` 不参与权限判断。
- 普通用户可直接注册；如需更高权限，可在自托管部署中通过服务端邀请机制启用受限角色。
- 用户可使用用户名或绑定邮箱登录，并在账号菜单中修改收件邮箱。
- 邀请码、JWT 密钥和数据库密码只能放在 `backend/.env` 或部署平台 Secret 中。

生产 HTTPS 环境应设置：

```env
AUTH_COOKIE_SECURE=true
CORS_ORIGINS=https://your-frontend.example
CORS_ORIGIN_REGEX=
```

## 邮件投递

任意合法邮箱都可作为收件地址。服务端通过统一 SMTP 账号发送，用户不需要向灵途提供自己的邮箱登录密码。

QQ 邮箱示例：

```env
SEND_REAL_EMAILS=true
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USERNAME=your_account@qq.com
SMTP_PASSWORD=your_qq_smtp_authorization_code
SMTP_FROM=your_account@qq.com
SMTP_SSL=true
EMAIL_QUOTA_ENABLED=true
EMAIL_USER_DAILY_LIMIT=10
EMAIL_IP_HOURLY_LIMIT=30
```

QQ 邮箱必须使用 SMTP 授权码，不能使用 QQ 登录密码。未配置 SMTP 或 `SEND_REAL_EMAILS=false` 时，后端进入 dry-run，前端会明确提示邮件未真实发送。

真实 SMTP 投递默认按认证用户每日 10 封、连接 IP 每小时 30 封限额。计数使用数据库原子事务，IP 只保存基于服务端认证密钥的 HMAC；dry-run 不消耗额度。生产环境应根据 SMTP 服务商配额调整上述参数。

## 后台桌面推送

后台通知使用 Service Worker、Push API、VAPID 和后端持久化订阅。订阅与认证用户关联，同一账号可在多台设备和多个浏览器分别订阅；退出登录或关闭开关时会取消当前浏览器订阅。行程保存成功后，后端以尽力而为方式发送推送，推送失败不会回滚行程；推送服务返回 404/410 时会自动删除失效订阅。

在 `backend/.env` 配置：

```env
WEB_PUSH_VAPID_PUBLIC_KEY=base64url_public_key
WEB_PUSH_VAPID_PRIVATE_KEY=base64url_private_key
WEB_PUSH_VAPID_SUBJECT=mailto:admin@example.com
WEB_PUSH_MAX_RETRIES=2
WEB_PUSH_RETRY_DELAY_SECONDS=0.25
WEB_PUSH_TTL_SECONDS=300
WEB_PUSH_TIMEOUT_SECONDS=15
WEB_PUSH_DNS_TIMEOUT_SECONDS=3
WEB_PUSH_MAX_SUBSCRIPTIONS_PER_USER=20
WEB_PUSH_DELIVERY_BUDGET_SECONDS=30
WEB_PUSH_ALLOWED_HOST_SUFFIXES=
```

公钥是浏览器创建订阅所需的公开值；私钥只能保存在后端 `.env` 或部署平台 Secret 中，绝不能提交 Git。更换 VAPID 密钥后，使用旧公钥创建的浏览器订阅需要重新开启。

运行限制：

- 服务端只接受 HTTPS 443 端口且 DNS 全部解析为公网 IP 的 Push endpoint，并禁止跟随 HTTP 重定向。
- `WEB_PUSH_ALLOWED_HOST_SUFFIXES` 可选地限定可信 Push 服务域名；还可通过订阅上限和总投递时间预算限制资源消耗。
- 首次开启时，用户必须主动授予通知权限；拒绝后需到浏览器的网站设置中重新允许。
- 生产环境必须使用 HTTPS；`localhost` 开发环境通常可直接使用。
- 页面关闭后能否收到通知取决于浏览器与操作系统的后台策略。
- Web Push 是尽力送达机制，不是保证送达的短信服务。

## 迁移与种子数据

创建或升级表结构：

```bash
cd backend
python -m alembic upgrade head
```

生成可选的合成分析数据：

```bash
python scripts/seed_travel_data.py --rows 1000
```

重新生成合成数据时可使用 `--reset`。该参数只删除脚本生成的 `seed_u_*` 数据，不删除注册用户及其个人旅行计划。

## 测试与构建

后端：

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q app scripts migrations
```

前端：

```bash
cd frontend
npm run build
```

要在 PostgreSQL 上运行后端测试，先设置 `DATABASE_URL` 并执行 `python -m alembic upgrade head`。测试使用随机账号并在结束后清理。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/register` | 注册并创建登录 Cookie |
| POST | `/api/auth/login` | 用户名或邮箱登录 |
| GET/PATCH | `/api/auth/me` | 读取账号或修改绑定邮箱 |
| POST | `/api/auth/logout` | 清除登录 Cookie |
| POST | `/api/trip/plan` | 生成、保存并可选邮件发送旅行计划 |
| GET | `/api/push/vapid-public-key` | 获取 Web Push VAPID 公钥 |
| POST | `/api/push/subscriptions` | 保存或更新当前用户的浏览器订阅 |
| DELETE | `/api/push/subscriptions` | 取消当前用户的浏览器订阅 |
| GET | `/api/trip/history` | 当前用户旅行历史 |
| GET/PUT | `/api/trip/history/{plan_no}` | 读取或修改当前用户计划 |
| POST | `/api/map/context` | 获取地图手册周边场所 |
| POST | `/api/agent/chat` | 多智能体自然语言分析 |
| POST | `/api/agent/analyze-file` | 分析旅行相关文档 |

受保护接口只使用后端会话中的用户身份；需要额外权限的分析能力默认面向自托管运营场景。

## 项目结构

```text
backend/
  app/
    agents/            旅行规划与数据分析智能体
    api/routes/        FastAPI 路由
    models/            Pydantic 请求与响应模型
    services/          地图、认证、数据库、邮件、Web Push 和计划持久化
    tools/             SQL、权限、图表、预测、报告等工具
  migrations/          Alembic 迁移
  scripts/             数据初始化与种子脚本
  tests/               后端自动化测试
frontend/
  src/
    components/        登录、智能分析、图表和地图手册
    services/          API、认证与短期计划缓存
    views/             规划首页与结果页
compose.yaml            本地 PostgreSQL
```

## 部署检查

- 使用 PostgreSQL 和独立低权限应用账号。
- 在发布新版本前执行 Alembic 迁移并备份数据库。
- 使用 HTTPS、精确 CORS 白名单和 `AUTH_COOKIE_SECURE=true`。
- 将 LLM、高德、SMTP、JWT 和数据库凭据放入 Secret 管理系统。
- 将 VAPID 私钥放入 Secret 管理系统，并确保前端获得的公钥与其匹配。
- 在反向代理层配置请求大小、登录限速、超时和访问日志。
- 对外部地图、LLM、图片、邮件和推送服务设置配额与监控。
- 不提交任何 `.env`、数据库文件、日志或导出文件。

## 参与贡献

欢迎提交 Issue 和 Pull Request。修改数据库结构时请同时提交 Alembic 迁移；修改用户流程时请补充对应测试，并确保 SQLite、PostgreSQL 和前端构建均通过。

## License

本项目采用 [MIT License](LICENSE)。
