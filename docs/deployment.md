# 生产部署指南

本文面向希望把灵途部署成可分享网站的维护者。普通使用者应访问部署后的网站地址，而不是 GitHub 源代码链接。

## 推荐拓扑

```text
用户浏览器
   │ HTTPS
   ▼
反向代理 / 托管平台
   ├── /            Vue 静态文件
   ├── /api/*       FastAPI
   └── /health      FastAPI 健康检查
                       │
                       ├── PostgreSQL
                       ├── LLM 服务
                       ├── 高德地图服务
                       └── SMTP / Web Push（可选）
```

推荐让前端和 API 使用同一域名，例如：

- 网站：`https://travel.example.com`
- API：`https://travel.example.com/api`

同源部署可以简化 Cookie、CORS 和浏览器安全策略。

## 1. 准备生产环境

- Linux 服务器或支持前后端服务的云平台
- Python 3.10+
- Node.js 18+
- PostgreSQL 14+
- 可配置 HTTPS 的域名
- 高德地图和 LLM 服务凭据

SQLite 适合本地体验，不建议用于长期多用户服务。

## 2. 配置后端

复制 `backend/.env.example` 为 `backend/.env`，至少设置：

```env
DATABASE_URL=postgresql+psycopg://lingtu_app:URL_ENCODED_PASSWORD@db-host:5432/lingtu_travel

LLM_API_KEY=...
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL_ID=...
AMAP_API_KEY=...

AUTH_SECRET_KEY=use_a_long_random_secret
AUTH_COOKIE_SECURE=true
CORS_ORIGINS=https://travel.example.com
CORS_ORIGIN_REGEX=
```

生成认证密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

不要将生产 `.env`、数据库密码、SMTP 授权码、VAPID 私钥或 API Key 提交到 Git。

## 3. 初始化并启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 2
```

生产环境应使用 systemd、Supervisor、容器编排或云平台进程管理保持服务运行。升级代码后先备份数据库，再执行 Alembic 迁移。

## 4. 构建前端

创建 `frontend/.env.production`：

```env
VITE_API_BASE_URL=
VITE_AMAP_WEB_JS_KEY=...
VITE_AMAP_SECURITY_JS_CODE=...
```

同源反向代理 `/api` 时，`VITE_API_BASE_URL` 保持为空。

```bash
cd frontend
npm ci
npm run build
```

将 `frontend/dist/` 发布到静态网站服务或反向代理的站点目录。

## 5. 上线前验证

```bash
curl -fsS https://travel.example.com/health
```

还应人工验证：

1. 注册、登录和退出登录
2. 未登录点击 AI 推荐或生成时被要求登录
3. 登录后可推荐目的地并生成行程
4. 生成结果进入当前账号历史计划
5. 另一个账号无法读取该计划
6. 地图、路线和图片正常加载
7. PDF/图片导出可用
8. 邮件与 Web Push（如果启用）能够失败隔离

## 6. 生产安全清单

- 强制 HTTPS，并设置 `AUTH_COOKIE_SECURE=true`
- 使用随机且独立的数据库密码与认证密钥
- CORS 只允许正式前端域名
- PostgreSQL 不直接暴露到公网
- 为数据库设置自动备份与恢复演练
- 在平台 Secret 中管理 LLM、地图、SMTP 和 VAPID 凭据
- 为 API、任务队列、磁盘和数据库连接设置监控
- 定期升级 Python、Node.js 和依赖包
- 对公开服务设置反向代理限流和请求大小限制

## 分享方式

- **给普通用户**：发送部署后的网站链接
- **给开发者**：发送 GitHub 仓库链接
- **用于演示**：建议单独部署测试环境，使用测试账号与受限额度的 API Key
