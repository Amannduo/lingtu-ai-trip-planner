# 灵途：基于多智能体的用户旅行画像与个性化行程推荐系统

灵途是一个面向课程设计的旅游领域多智能体系统。项目以“用户旅行计划数据”为核心数据集，结合 FastAPI、LangGraph/LangChain 风格 Agent、SQLite、Vue3、ECharts，实现旅行规划、用户画像、自然语言数据分析、权限控制、敏感词过滤和个性化推荐。

## 核心能力

- 旅行计划生成：根据城市、日期、预算、交通、住宿和偏好生成多日行程。
- 本地旅行数据集：SQLite 存储 10000 条真实模拟旅行计划数据。
- 用户画像分析：根据用户历史旅行计划汇总兴趣标签、常去城市、预算和旅行者类型。
- 多智能体分析：SecurityAgent、RoleAgent、RouterAgent、SQLAgent、ProfileAgent、RecommendationAgent、PredictAgent、EmailAgent、ChartAgent、ReportAgent 协同处理自然语言问题。
- 权限控制：guest、user、manager、admin 不同角色拥有不同查询权限。
- 敏感词过滤：手机号、邮箱、联系人、危险 SQL 等请求会被拦截。
- ECharts 图表：智能分析弹窗中返回 `table + chart + result`。
- 登录/注册演示：前端本地账号用于课程演示角色差异。
- 邮件工具：支持 SMTP；未配置时自动 dry-run，生成邮件内容但不真实发送。
- 文件分析：支持 TXT、MD、PDF、DOCX、XLSX 上传分析。

## 当前数据集

默认数据库文件：

```text
backend/data/travel.db
```

核心表：

```text
travel_plans   旅行计划明细
user_profiles  用户画像汇总
audit_logs     Agent 权限/调用审计日志
query_logs     自然语言查询日志
```

当前数据规模：

```text
travel_plans: 10000 条
user_profiles: 4545 个用户画像
目的地城市: 35 个
出发城市: 30 个
数据来源: realistic_synthetic
```

数据生成规则考虑了城市热度、季节、出发地、旅行天数、出行人数、预算、住宿、交通、用户偏好和旅行者类型，不使用 `u_current`、`test_user` 等测试数据。

重新生成或补齐数据：

```bash
conda activate LINGTU
python backend/scripts/init_travel_sqlite.py --rows 10000
```

如需清空后重建：

```bash
python backend/scripts/init_travel_sqlite.py --rows 10000 --reset
```

## 演示账号

登录功能为课程演示用，本地保存在浏览器 `localStorage`。

```text
普通用户: user / 123456       -> u_0001
经理:     manager / 123456    -> u_0100
管理员:   admin / 123456      -> u_0002
```

注册规则：

```text
普通用户: 可直接注册
经理:     需要经理授权码
管理员:   需要管理员授权码
```

默认演示授权码可在 `frontend/.env` 配置：

```env
VITE_MANAGER_INVITE_CODE=LINGTU_MANAGER_2026
VITE_ADMIN_INVITE_CODE=LINGTU_ADMIN_2026
```

这样可以展示“高权限角色需要授权”的权限控制流程，避免普通用户随意注册为 manager/admin。

未登录状态下：

```text
不会展示个人历史记录
生成行程不会写入用户数据集
智能分析会提示先登录
```

## 技术栈

后端：

```text
FastAPI
HelloAgents / SimpleAgent
LangGraph
SQLite
ECharts option 生成工具
高德地图服务
火山引擎联网问答 Agent，可选
FlyAI/飞猪预算数据源，可选
```

前端：

```text
Vue 3
TypeScript
Vite
Ant Design Vue
ECharts
Axios
高德地图 JS API
```

## 项目结构

```text
backend/
  app/
    agents/
      graph/travel_agent_graph.py
      trip_planner_agent.py
      destination_recommender_agent.py
    api/routes/
      trip.py
      agent.py
      map.py
    services/
      schema.py
      database_service.py
      travel_plan_data_service.py
      transport_budget_service.py
    tools/
      sql_agent_tool.py
      llm_sql_agent_tool.py
      profile_tool.py
      chart_tool.py
      permission_tool.py
      sensitive_filter_tool.py
      predict_tool.py
      send_email_tool.py
      file_analysis_tool.py
  scripts/
    init_travel_sqlite.py
  data/
    travel.db

frontend/
  src/
    components/
      AgentAssistantModal.vue
      AuthDialog.vue
      AgentChart.vue
    views/
      Home.vue
      Result.vue
    services/
      api.ts
      auth.ts
```

## 快速启动

后端：

```bash
conda activate LINGTU
cd backend
pip install -r requirements.txt
copy .env.example .env
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
copy .env.example .env
npm run dev
```

访问：

```text
前端: http://127.0.0.1:5173/
后端文档: http://127.0.0.1:8000/docs
```

## 主要接口

```text
POST /api/trip/plan
生成旅行计划；登录后会把结果写入 travel_plans 并刷新 user_profiles。

GET /api/trip/history
查询当前用户历史旅行计划。

POST /api/agent/chat
自然语言智能分析，返回 table、chart、result、permission、sensitive。

POST /api/agent/analyze-file
上传文件并进行旅行相关信息分析。
```

智能分析请求示例：

```json
{
  "user_id": "u_0002",
  "role": "admin",
  "message": "统计最热门旅游城市",
  "email": "demo@example.com"
}
```

返回结构：

```json
{
  "success": true,
  "intent": "city_rank",
  "agent": "SQLAgent",
  "tool": "sql_agent_tool",
  "table": [],
  "chart": {},
  "result": "当前数据中最热门的目的地是成都，共有397条旅行计划。",
  "permission": {
    "role": "admin",
    "allowed": true,
    "reason": ""
  }
}
```

## 示例问题

```text
分析我的旅行兴趣画像
和我相似的用户最喜欢去哪些城市
统计不同城市的平均预算
生成热门目的地 Top10 图表
预测下个月最热门的旅游城市
查询所有用户手机号
把我的画像报告发送到邮箱
```

## 环境变量

后端参考：

```text
backend/.env.example
```

前端参考：

```text
frontend/.env.example
```

当前项目默认使用 SQLite，不需要 PostgreSQL 或 `DATABASE_URL`。

## 验证命令

```bash
cd frontend
npm run build
```

```bash
conda activate LINGTU
python backend/scripts/init_travel_sqlite.py --rows 10000
```

## 课程设计对应关系

```text
选择领域: 旅游
数据集: 用户旅行计划数据，10000 条真实模拟记录
后端: FastAPI
多智能体: Security/Role/Router/SQL/Profile/Recommendation/Predict/Email/File/Chart/Report
工具: sql_agent_tool、send_email_tool、chart_tool、profile_tool、permission_tool、sensitive_filter_tool
权限: guest/user/manager/admin
敏感词: 手机号、邮箱、联系人、危险 SQL
前端展示: 弹窗式智能分析，表格 + ECharts 图表 + 中文结论
```
