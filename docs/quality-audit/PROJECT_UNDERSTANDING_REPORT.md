# 项目理解报告

> 生成时间：2026-07-25
> 分析范围：仓库 `E:\agent\agent1`（文档 + 代码交叉核对，本阶段未修改业务代码）

---

## 1. 项目概述

**灵途 AI 旅行规划师（Lingtu AI Trip Planner）** 是一个面向真实旅行场景的开源 Web 应用。用户填写出发地/目的地、日期、预算、交通、住宿与偏好后，系统通过多智能体工作流 + 高德地图 + LLM 生成多日行程，并支持历史记录、地图手册、PDF 导出、邮件投递和浏览器 Web Push。

| 维度 | 说明 |
| --- | --- |
| 目标用户 | 个人旅行者；自托管部署的运营/分析角色（manager/admin） |
| 核心问题 | 手工规划行程耗时，且景点坐标、路线、预算、天气等事实易失真 |
| 主流程 | 填表 →（可选）智能推荐目的地 → 生成行程 → 查看/编辑/导出/邮件/推送 |
| 阶段判断 | **活跃开发 + 硬化（hardening）中**：大量未提交改动（质量门、语义契约、限流、安全边界测试），有较完整后端测试与 CI，**接近可发布但仍在迭代** |

用户最主要使用流程：

1. 打开首页填写旅行表单（可匿名）
2. 可选：对话式目的地推荐
3. 提交生成（同步 `/api/trip/plan` 或异步 `/api/trip/plan-jobs` + SSE）
4. 结果页查看行程、地图、预算；登录用户可落库与再编辑
5. 可选：导出 PDF、发邮件、开启 Web Push

---

## 2. 技术架构

| 层级 | 使用技术 | 主要职责 |
| --- | --- | --- |
| 前端 | Vue 3、TypeScript、Vite 6、Ant Design Vue 4、ECharts、jsPDF/html2canvas、AMap JS API | 表单、结果展示、导出、登录态、本地草稿、Push 订阅 |
| 后端 | FastAPI、Pydantic v2、LangGraph、hello-agents、uvicorn | API、鉴权、行程规划编排、质量门、分析智能体 |
| 数据库 | SQLite（默认）/ PostgreSQL 17、SQLAlchemy 2、Alembic | 用户、行程、Push 订阅、邮件配额、审计与查询日志 |
| 第三方服务 | 高德地图、OpenAI 兼容 LLM、智谱 Web Search、火山引擎 Agent、FlyAI/飞猪 CLI、Unsplash、SMTP、Web Push/VAPID | POI/路线/天气、生成内容、预算票价、配图、投递 |
| 部署方式 | 本地双进程（uvicorn + vite）；`compose.yaml` 仅起 PostgreSQL；GitHub Actions CI | 开发与 CI 矩阵（sqlite/postgresql + frontend build） |

**架构特征：**

- 单体前后端分离，**非微服务**
- 进程内限流与任务容量（`request_rate_limit_service`、`BoundedSemaphore`），**无 Redis/消息队列**
- 行程生成可同步或后台线程 + SSE；邮件/Push 为保存后 best-effort 副作用
- Cookie `HttpOnly` 会话 + 可选 Bearer；权限以服务端角色为准

---

## 3. 项目模块

| 模块 | 主要功能 | 重要文件 | 风险等级 |
| --- | --- | --- | --- |
| 行程生成 | 多日计划编排、地图 enrichment、质量门 | `trip_planner_agent.py`, `trip_planning_graph.py`, `trip.py` | **高** |
| 语义契约 | 自由文本约束解析、冲突/待确认、硬拦截 | `semantic_contract_service.py`, `schemas.py` | **高** |
| 质量门 | 发布前事实与预算校验 | `trip_plan_quality_service.py` | **高** |
| 异步任务 | plan-jobs、SSE、取消、超时 | `trip_generation_job_service.py`, `trip.py` | **高** |
| 认证权限 | 注册登录、角色邀请码、JWT cookie | `auth_service.py`, `routes/auth.py` | **高** |
| 行程持久化 | 历史读写、ETag/乐观锁相关逻辑 | `travel_plan_data_service.py` | **中高** |
| 地图/POI | 搜索、路线、天气、手册周边 | `amap_service.py`, `routes/map.py`, `poi.py` | **中** |
| 预算交通 | FlyAI 票价/酒店引用、校验 | `transport_budget_service.py` | **中高** |
| 联网攻略 | 智谱搜索 + 本地降级 | `zhipu_search_service.py`, `web_travel_guide_agent.py` | **中** |
| 邮件/Push | SMTP dry-run/实发、配额、VAPID | `trip_email_service.py`, `web_push_service.py` | **中** |
| 分析智能体 | RBAC SQL/图表/文件分析 | `travel_agent_graph.py`, `routes/agent.py` | **高** |
| 目的地推荐 | 对话推荐与 form_patch | `destination_recommender_agent.py` | **中** |
| 前端 UX | Home/Result、缓存、导出 | `Home.vue`, `Result.vue`, `api.ts` | **中** |
| 部署/配置 | env、compose、CI | `.env.example`, `compose.yaml`, `ci.yml` | **中** |

---

## 4. 核心业务流程

### 流程 A：注册 / 登录

| 项 | 内容 |
| --- | --- |
| 参与角色 | 访客 → 普通用户；manager/admin 需邀请码 |
| 前置条件 | `AUTH_SECRET_KEY` ≥ 32 字符；DB 可写 |
| 主要步骤 | `POST /api/auth/register|login` → Argon2 校验 → 写 `HttpOnly` Cookie |
| 成功结果 | Cookie 会话；`/api/auth/me` 可取身份 |
| 失败结果 | 400/401；中间件限流 register 10/h、login 20/5min |
| 涉及模块 | `auth_service`, `routes/auth`, `users` 表 |
| 可能风险 | 角色伪造、弱密码、邀请码配置不当、token_version 吊销一致性 |

### 流程 B：填写表单并生成行程（核心）

| 项 | 内容 |
| --- | --- |
| 输入 | 城市、日期、天数、人数、预算、交通、住宿、偏好、自由文本、语义契约确认标志 |
| 中间执行 | Pydantic 校验 → 语义硬拦截 → 限流 → Agent/Graph 规划 → 高德 enrichment → 质量评估 → 可选落库 → 邮件/Push |
| 输出 | `TripPlanResponse`（景点/日程/预算/天气/quality 等） |
| 数据保存 | 登录用户：`travel_plans`；匿名：仅前端本地缓存 |
| 失败处理 | 422 质量/语义拒绝；429 限流/容量；500 生成失败（日志含异常） |
| 状态转换 | 请求中 → 成功/失败；异步 job：queued/running/completed/failed/cancelled |
| 权限 | 生成可匿名；保存/邮件需登录 |
| 不可逆 | 真实 SMTP/Push 一旦发出不可撤回；生成消耗 LLM/地图额度 |

### 流程 C：异步 plan-jobs + SSE

| 项 | 内容 |
| --- | --- |
| 步骤 | `POST /plan-jobs` → cookie 绑定 job token → `GET .../events` SSE → 可选 cancel |
| 所有权 | `user:{id}` 或 `anonymous:{ip}` + job cookie |
| 风险 | 任务泄漏、超时后晚到结果落库、SSE 断连恢复、容量耗尽 |

### 流程 D：历史查看与编辑

| 项 | 内容 |
| --- | --- |
| 前置 | 登录 |
| 步骤 | `GET /history` → `GET/PUT /history/{plan_no}` |
| 权限 | **严格按 `user_id` 过滤**，跨用户 404 |
| 风险 | 信任客户端改写服务端权威字段、ETag 竞态、脏 plan_json |

### 流程 E：目的地推荐

| 项 | 内容 |
| --- | --- |
| 接口 | `POST /api/recommend` |
| 特点 | 对话消息 + context；推断人数/周末/可行性圈 |
| 风险 | LLM 幻觉远距离目的地、覆盖 form_confirmed 字段 |

### 流程 F：邮件与 Web Push

| 项 | 内容 |
| --- | --- |
| 邮件 | 生成完成后可选；配额 user 日 10 / IP 时 30；失败不回滚行程 |
| Push | 需登录 + VAPID；best-effort；无效订阅清理 |
| 风险 | 配额竞态、SSRF 类 endpoint、配额绕过 |

### 流程 G：RBAC 分析与文件分析

| 项 | 内容 |
| --- | --- |
| 接口 | `/api/agent/chat`, `/analyze-file`, `/capabilities` |
| 权限 | user 个人范围；manager 匿名聚合；admin 全局（设计文档 + 测试） |
| 风险 | SQL 越权、敏感字段、上传炸弹、容量耗尽 |

---

## 5. 核心业务规则

| 规则编号 | 业务规则 | 代码位置 | 测试重点 |
| --- | --- | --- | --- |
| R01 | 日期 YYYY-MM-DD；end≥start；travel_days = 日期差+1 | `schemas.TripRequest` | 不一致天数、跨月年 |
| R02 | 天数 1–30；人数 1–20；偏好最多 10 条、每条 ≤32 | `schemas.py` | 边界与超长 |
| R03 | 密码 ≥8 且含字母+数字；用户名 3–32 特定字符集 | `auth_service` | 弱密码、特殊用户名 |
| R04 | manager/admin 注册必须服务端邀请码 | `auth_service._validate_role_invite` | 角色伪造 |
| R05 | 前端 role/user_id 不参与鉴权 | `api/auth.py`, architecture | 水平/垂直越权 |
| R06 | 行程历史读写必须匹配 `user_id` | `travel_plan_data_service` | 水平越权 |
| R07 | 质量门：publishable、score、非 map_fallback 等 | `trip_plan_quality_service`, `_plan_is_publishable` | 降级计划不可发布 |
| R08 | 语义契约硬拦截可配置；确认标记放行 | `config.semantic_contract_hard_block_enabled` | 422 vs 放行 |
| R09 | 行程生成限流默认 10/60s（用户或 IP） | `trip.py` + settings | 429 + Retry-After |
| R10 | 生成总时长预算默认 600s | `trip_generation_max_runtime_seconds` | 超时无落库 |
| R11 | JSON body ≤1MB；文件分析 ≤20–25MB | `main.py` middleware | 413 |
| R12 | 注册 10/h、登录 20/5min 中间件限流 | `main._rate_limit_rule` | 暴力破解 |
| R13 | 邮件失败不阻断保存 | `trip.py` email 分支 | 副作用隔离 |
| R14 | 仅首个 bootstrap admin 可认领 `u_current` 遗留行程 | `auth_service._can_claim_legacy_plans` | 数据归属 |
| R15 | form_confirmed/user_explicit 不可静默覆盖 | `semantic_contract_service` | 字段保护 |
| R16 | 业务时区默认 Asia/Shanghai（周末语义） | `business_timezone` | 跨时区周末 |
| R17 | FlyAI 默认关闭（.env.example）；CLI 环境白名单 | `transport_budget_service` 等 | 命令注入面 |
| R18 | Web Push endpoint 主机后缀白名单 | `web_push_service` | SSRF |
| R19 | 分析智能体按角色行级范围 | `analytics_context_tool`, agent graph | 垂直越权 |
| R20 | Cookie SameSite=Lax；跨站 cookie 写拒绝测试存在 | `auth` + hardening tests | CSRF |

---

## 6. 外部依赖

| 依赖 | 用途 | 失败影响 | 降级 | 测试 Mock |
| --- | --- | --- | --- | --- |
| 高德 AMAP | POI/路线/天气 | 行程坐标/路线不可信，质量门可能拒绝 | 有限 fallback（易被质量门拦） | 是 |
| LLM（OpenAI 兼容） | 行程/推荐/分析 | 核心生成失败 | 部分规则 fallback | 是 |
| 智谱 Web Search | 联网攻略 | 攻略降级本地 | 是 | 是 |
| 火山引擎 Agent | 联网问答（可选） | 关闭即可 | 配置关闭 | 是 |
| FlyAI CLI | 票价/酒店参考 | 预算未验证标记 | 关闭/启发式 | 是 |
| Unsplash | 景点配图 | 无图 | 可空 | 可选 |
| SMTP | 邮件 | 仅投递失败 | dry-run | 是 |
| Web Push | 通知 | 无推送 | best-effort | 是 |
| PostgreSQL | 生产库 | 无持久化 | 可回退 SQLite | CI 双库 |

---

## 7. 当前测试情况

| 项 | 情况 |
| --- | --- |
| 测试类型 | 后端 pytest 单元/集成/HTTP 契约/安全边界；**无前端单元测试**；有脚本级 e2e/probe（`scripts/e2e_real.py` 等） |
| 规模 | **26** 个 `test_*.py`，约 **351** 个 `test_` 函数 |
| 已覆盖 | 鉴权邮箱、质量门、语义契约、预算/FlyAI、plan-jobs、限流容量、RBAC 分析、Web Push、天气 fallback、hardening/trust boundary |
| 缺口 | 前端组件/E2E 自动化；真实 LLM/高德 e2e（需密钥）；正式压测；PostgreSQL 本机（依赖 Docker） |
| skip/xfail | **未发现** `@pytest.mark.skip` / `xfail` |
| 配置 | `requirements-dev.txt` 仅 pytest；CI：alembic + pytest + compileall + frontend build |
| 本地状态 | `backend/.env` 与 `frontend/.env` 已存在；**无 `backend/.venv`**（需系统 Python 或新建 venv）；`frontend/node_modules` 已安装 |

---

## 8. 高风险区域

### 高

1. **行程生成链路**（LLM + 多服务 + 质量门）：状态多、失败模式多、易脏数据
2. **异步 job + 取消/超时**：晚到结果、权限 cookie、并发容量
3. **语义契约硬拦截**：误拦/漏拦直接影响可用性与正确性
4. **RBAC 分析 SQL**：数据泄露面
5. **认证与历史隔离**：水平越权、角色邀请码

### 中

6. 预算/票价引用与人数倍率
7. 邮件配额原子性与 SMTP 配置错误提示
8. Push 订阅绑定与 endpoint 校验
9. 前后端状态：本地草稿 vs 服务端历史
10. 限流仅进程内，多 worker 不一致

### 低

11. 静态导出 PDF 布局
12. Unsplash 配图缺失
13. 文档小差异

---

## 9. 文档与代码不一致情况

| 项 | 文档 | 代码实际 |
| --- | --- | --- |
| 根目录 `.env.example` | 快速开始常写 Copy-Item .env.example | 根目录仅为 **Postgres 变量**；后端密钥在 `backend/.env.example` |
| VOLCENGINE_AGENT_TIMEOUT | `.env.example` 写 30 | `config.py` 默认 **120** |
| 异步 plan-jobs | README 主接口表未列 | 代码已有 `/api/trip/plan-jobs` 等完整 API |
| README 主接口表 | 未列 recommend/poi 细节 | 存在 `/api/recommend`、`/api/poi/*` |
| 前端测试 | 文档强调 pytest + build | 前端 **无 test script** |
| hello-agents 路径 | config 会尝试加载上级 HelloAgents/.env | 本仓库独立，路径可能不存在（无害） |
| 健康检查 | `/health` | 另有 `/api/trip/health`、`/api/agent/health` 等 |

---

## 10. 初步判断

| 判断 | 结论 |
| --- | --- |
| 是否具备测试条件 | **是**：测试集完整、CI 明确、依赖声明齐全 |
| 是否能够启动 | **大概率可以**（env 已存在）；需验证 `AUTH_SECRET_KEY`/`AMAP` 与 Python 依赖安装；无 venv 需确认全局/其他环境 |
| 结构/配置问题 | 无 venv；第三方密钥决定“完整生成”能否成功；进程内限流非集群安全 |
| 下一阶段重点 | 1）环境可运行性 2）全量 pytest 3）鉴权/越权/校验主动探测 4）质量门与语义契约边界 5）job/限流/安全 6）前端构建与关键 API 冒烟 |

---

*本报告为质量检查第二阶段交付物；后续阶段将据此执行测试方案。*
