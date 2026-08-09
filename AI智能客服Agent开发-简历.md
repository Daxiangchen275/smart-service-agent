# AI Agent 应用开发工程师 — 个人简历

## 项目经验

### 企业智能客服多 Agent 系统 <span style="font-weight:normal;font-size:small;">2026.07 — 2026.08</span>

**场景**：企业客服场景中，订单/物流查询、工单处理、知识问答、合规审查等高频操作依赖人工，存在回复不一致、合规风险、重复劳动、高峰排队等问题。需用多 Agent 协作自动完成意图理解、知识检索、工单处理与内容审查，实现 7×24 智能客服。

**架构**：采用 Supervisor 编排模式，落地 IntentRouter（意图路由 + 上下文改写）/ KnowledgeRAG（Query Rewrite → ChromaDB 召回 → Cross-Encoder 重排 → LLM 生成）/ TicketHandler（工单 CRUD + 自动匹配 + 去重）/ ToolExecutor（订单/用户/风控 Skill工具调用）/ ComplianceChecker（规则引擎 + LLM 深度审查）五 Agent 协作，Supervisor 汇总融合多回复；配套工作记忆 + 对话历史 Markdown 持久化 + ChromaDB 向量库三级记忆系统。

**数据**：自建 MySQL 业务数据集，覆盖用户、订单、工单、物流、支付 5 类核心表，含正常流程与退款/投诉等异常边界场景；配套意图识别、实体提取、RAG 检索、端到端 4 类评测用例，支持回归验证。

**指标**：Recall@5、Faithfulness、P95 延迟、首 Token 延迟(TTFT)、工具调用成功率；辅以路由准确率、Token 消耗（按 Agent 维度聚合）、合规通过率、意图分布等 30+ 指标，基于自研 MetricsCollector（滑动窗口 avg/p50/p95/p99）+ OpenTelemetry 全链路采集。

**优化**：
- Query Rewrite 提升口语化查询的向量检索精度，配合 Cross-Encoder 重排提升 Top-3 忠实度
- 双模型架构：内部调用走非流式模型确保 Token 统计准确，用户可见回复走流式模型保留 SSE 打字机体验
- Bigram Overlap 系数替代单字 Jaccard 做标题相似度去重，解决中英文混排措辞差异导致的重复建单
- LLM Prompt 规则 + 代码校验双保险拦截"我要投诉"类模糊消息，改为追问而非创建空工单
- 订单关键词自动匹配：无订单号投诉通过产品类别加权 + 状态优先级自动关联用户进行中订单
- 数据归属权双层防御：Store 层 SQL WHERE 过滤 + Compliance 层二级校验

**取舍**：Top-5 召回后精排至 Top-3，牺牲部分召回覆盖换低延迟与更高忠实度；合规侧宁可误拦不漏放，PII 正则 + 敏感词 + LLM 语义三级审查保障电商在线体验与风控底线；流式模型牺牲 Token 统计精度换用户侧打字机实时体验。

**复盘**：下一步接入在线点赞/点踩反馈与持续评估集，打通 badcase → 标注 → 回归的闭环；将 Skill Runtime 从子进程调用升级为 MCP 协议实现工具热插拔；引入 Redis 会话缓存降低冷启动延迟。

**技术栈**：`Python` `LangGraph` `LangChain` `FastAPI` `MySQL 8.0` `ChromaDB` `Docker Compose` `OpenTelemetry` `DeepSeek API` `阿里云 DashScope` `SSE Streaming` `Prompt Engineering`
