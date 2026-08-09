# 智能客服多 Agent 系统

基于 **LangGraph Supervisor 架构**的智能客服系统，集成 6 个专业化 Agent 协作，支持意图识别、知识检索、工单处理、合规审查的完整闭环。

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue" alt="Python">
  <img src="https://img.shields.io/badge/LangChain-0.3+-green" alt="LangChain">
  <img src="https://img.shields.io/badge/LangGraph-0.2+-orange" alt="LangGraph">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-teal" alt="FastAPI">
  <img src="https://img.shields.io/badge/MySQL-8.0-blue" alt="MySQL">
  <img src="https://img.shields.io/badge/ChromaDB-0.5+-purple" alt="ChromaDB">
</p>

---

## 架构总览

```
用户消息 → IntentRouter → TicketHandler / ToolExecutor / KnowledgeRAG
                              ↓
                        ComplianceChecker
                              ↓
                          Supervisor → SSE 流式回复
```

### 6 Agent 协作

| Agent | 职责 | 技术 |
|---|---|---|
| **IntentRouter** | 意图分类 + 上下文指代消解 | LLM JSON 结构化输出 + 正则兜底 |
| **KnowledgeRAG** | 知识库检索 + 答案生成 | Query Rewrite → ChromaDB 召回 → Reranker 重排 → LLM 生成 |
| **TicketHandler** | 工单创建/查询/更新 | LLM 分析 + 订单自动匹配 + Bigram 去重 + 信息不足拦截 |
| **ToolExecutor** | 订单/用户/风控查询 | Skill Runtime 子进程调用 → LLM 自然语言转译 |
| **ComplianceChecker** | 合规审查 + 数据归属权 | 规则引擎 + LLM 深度审查两阶段 |
| **Supervisor** | 多回复融合、工作记忆更新 | LLM 去重整合 |

---

## 快速开始

### 环境要求

- Python 3.11+
- Docker & Docker Compose
- DeepSeek API Key
- 阿里云 DashScope API Key（Embedding + Reranker）

### 1. 克隆项目

```bash
git clone <repo-url>
cd smart-service-agent/python-impl
```

### 2. 配置环境

```bash
cp .env.example .env
# 编辑 .env，填入你的 API Key
```

关键配置项：

```ini
LLM_API_KEY=sk-your-deepseek-key
RAG_EMBEDDING_API_KEY=sk-your-dashscope-key
RAG_RERANKER_API_KEY=sk-your-dashscope-key
MYSQL_PASSWORD=your-db-password
```

### 3. Docker 一键启动

```bash
docker-compose up -d
```

这会自动完成：
- 启动 MySQL 8.0 → 执行建表脚本 + 种子数据
- 构建应用镜像 → 启动 FastAPI → 初始化 ChromaDB 知识库
- 开放 `http://localhost:8000`

### 4. 本地开发模式

```bash
# 安装依赖
pip install -r requirements.txt

# 确保本地 MySQL 已运行，执行建表
mysql -u root -p < scripts/init_mysql.sql

# 启动
python api/main.py
```

### 5. 验证

```bash
# 健康检查
curl http://localhost:8000/health

# Swagger API 文档
open http://localhost:8000/docs

# 前端聊天界面
open http://localhost:8000
```

---

## 核心特性

### RAG 检索增强生成

```
用户问题 → Query Rewrite → ChromaDB 向量召回(5) → Cross-Encoder 重排(Top-3) → LLM 生成
```

- 支持 PDF / Word / Excel / TXT 多格式知识库
- 服务启动时自动构建向量索引
- 兼容 DashScope Rerank API

### 工单智能处理

- **信息不足拦截**："我要投诉"类模糊消息自动追问而非创建空工单
- **订单自动匹配**：无订单号投诉通过关键词匹配关联用户进行中订单
- **去重检测**：Bigram Overlap 系数检测标题相似，避免重复建单
- **数据归属权**：SQL 过滤 + Compliance 校验双层防御

### 流式输出

- FastAPI SSE 端点，打字机效果
- `astream_events` v2 节点级流控
- 自动过滤内部 JSON 分析输出，用户只看到自然语言

### 可观测性

访问 `/api/metrics` 获取 30+ 实时指标：

```json
{
  "uptime_seconds": 3600,
  "requests": { "total": 128, "ttft": { "avg": 320, "p50": 280 } },
  "intent": { "distribution": { "complaint": 45, "transaction": 38 } },
  "llm": { "input_tokens": 125000, "output_tokens": 48000 },
  "tools": { "order_query": { "calls": 52, "success_rate": 0.98 } },
  "rag": { "search_latency": { "avg": 85, "p95": 150 } }
}
```

### 评测框架

```bash
python -m evals.runner
```

覆盖意图识别、实体提取、RAG 检索、端到端 4 类评测用例。

---

## 技术栈

| 层级 | 技术 |
|---|---|
| Agent 编排 | LangGraph StateGraph + MemorySaver |
| LLM | DeepSeek (via LangChain ChatOpenAI) |
| Embedding | 阿里云 DashScope text-embedding-v3 |
| Reranker | 阿里云 DashScope gte-rerank-hybrid |
| 向量库 | ChromaDB |
| 数据库 | MySQL 8.0 + aiomysql |
| API | FastAPI + SSE Streaming |
| 可观测性 | OpenTelemetry + 自研 MetricsCollector |
| 部署 | Docker Compose |
| 评测 | 自研 E2E 评测框架 |

---

## 项目结构

```
python-impl/
├── agents/                  # 6 个 Agent
│   ├── supervisor.py        # LangGraph 图编排
│   ├── intent_router.py     # 意图分类 + 上下文改写
│   ├── knowledge_rag.py     # RAG 检索增强
│   ├── ticket_handler.py    # 工单处理
│   ├── tool_executor.py     # 工具执行
│   └── compliance_checker.py # 合规审查
├── skills/                  # Skill Runtime 工具
│   ├── ticket-handler/      # 工单 CRUD 脚本
│   └── tool-executor/       # 查询工具脚本
├── tracing/                 # 可观测性
│   ├── collector.py         # 指标采集器
│   ├── otel_config.py       # Token 追踪 + OpenTelemetry
│   └── middleware.py         # 监控中间件
├── memory/                  # 三级记忆系统
├── rag/                     # Embedding + Reranker
├── services/stores.py       # 数据访问层
├── api/main.py              # FastAPI 入口
├── api/static/index.html    # 前端 Chat UI
├── evals/                   # 评测框架
├── scripts/                 # 工具脚本
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/chat` | POST | 普通聊天接口 |
| `/api/chat/stream` | POST | SSE 流式聊天 |
| `/api/metrics` | GET | 监控指标 |
| `/health` | GET | 健康检查 |
| `/` | GET | 聊天前端页面 |
| `/docs` | GET | Swagger 文档 |

---

## License

MIT
