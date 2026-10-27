# 析数：可验证的电商数据分析智能体

当前阶段：已完成可运行的首版核心功能。

2026-10-14 升级：模型接入与工具协议使用 LangChain `ChatOpenAI.bind_tools`，业务工具使用 LangGraph `ToolNode/ToolRuntime`。支持完整多调用、受限并发、标准销售指标、最终结果选择与 SSE 自动重连。Python 回归 107/107、前端 4/4；框架首轮独立复核 12/13（92.3%），最终定向复测 5/5。最终复测证据见 [独立复核结果](evaluations/results/framework-upgrade-final-20261014/independent-review.json)。不同版本与定向复测成绩不能合并宣称总体准确率 100%。

使用已有 Conda 环境 `langgraph` 与 Docker PostgreSQL 数据库 `agent_analytics`。依赖清单为 `requirements.txt`；本地 `.env` 已写入实际连接信息及随机密钥，并被 Git 忽略。

## 目录结构

```text
agent-project/
├── backend/                     # Python 后端包
│   ├── main.py                  # FastAPI 应用入口
│   ├── core/                    # 配置、数据库连接、认证基础设施
│   ├── api/routes/              # 认证、数据集、分析任务、产物、健康检查接口
│   ├── schemas/                 # API 请求和响应结构
│   ├── models/                  # 用户、数据集、任务和四张业务表
│   ├── repositories/            # 数据库访问
│   ├── services/                # 数据集与分析任务业务规则
│   ├── ingestion/               # 文件解析、字段映射、导入校验
│   ├── agent/
│   │   ├── state.py             # Agent 状态定义
│   │   ├── graph.py             # LangGraph 流程编排
│   │   ├── nodes/               # 决策、工具、澄清、校验、结束节点
│   │   └── prompts/             # 提示词文件
│   ├── tools/                   # 数据字典、SQL、Python、绘图工具接口
│   ├── execution/               # SQL 执行、权限限制、Python 沙箱
│   ├── reporting/               # 报告构建和结果依据校验
│   ├── context/                 # 会话原文、增量摘要和请求预算
│   ├── memory/                  # 偏好、常问模式、Milvus 同步与召回
│   └── workers/                 # Celery 配置和后台任务
├── frontend/                    # 工作台页面、样式和交互
├── migrations/                  # 初始建表 SQL
├── tests/
│   ├── unit/                    # 单元测试
│   ├── integration/             # 接口、数据库及任务流程测试
│   └── security/                # 数据隔离与执行权限测试
├── evaluations/
│   ├── cases/                   # 评测任务与标准结果
│   └── results/                 # 本地评测输出
├── scripts/                     # 数据导入和模拟数据生成脚本
├── sandbox/                     # 受限 Python 沙箱镜像与执行脚本
└── storage/
    ├── uploads/                 # 原始上传文件
    ├── artifacts/               # 结果表、图表、报告和脚本
    └── traces/                  # 持久执行记录
```

## 模块边界

- API 处理请求、身份校验和响应；Service 管理业务规则。
- Repository 负责数据库访问；Model 与 Schema 分别描述存储和接口结构。
- Agent 决定分析步骤；Tools 描述工具接口；Execution 落实实际执行和权限边界。
- Worker 执行后台任务；Reporting 组织报告并核对计算依据。
- `storage/` 与 `evaluations/results/` 保存运行产生的本地文件，实际内容不提交。

## 已实现功能

账号注册登录、用户资源隔离、CSV/XLSX 预览和字段映射、四表原子导入、模拟数据、LangGraph 工具循环、受限 SQL、Python 容器、绘图、澄清恢复、任务取消、持久事件和 SSE、CSV/PNG/Markdown 产物下载。

**演示模式使用规则流程，不调用聊天模型；记忆索引与相关模式召回可能调用向量模型。** 真实模型通过兼容 Chat Completions 的 `tools` 协议自主调用工具。目前本地已配置硅基流动 `deepseek-ai/DeepSeek-V4-Flash`；密钥仅保存在被忽略的 `.env`，示例模板不包含真实密钥。

## 启动与使用

在项目根目录使用 PowerShell：

```powershell
conda activate langgraph
# 当前环境、数据库表和沙箱镜像已准备；下列三项用于重新准备。
python -m pip install -r requirements.txt
python scripts/init_db.py
python scripts/init_memory.py
docker build -t agent-analytics-sandbox:local sandbox

# 启动 API、两个分析 Worker、一个记忆 Worker 和 Beat，默认端口 8001。
.\scripts\start.ps1 -Workers 2
```

打开 [本地工作台](http://127.0.0.1:8001)，创建自己的账号，点击“加载模拟示例”，等待“已就绪”，提交“品类变化”问题。API 文档在 [Swagger](http://127.0.0.1:8001/docs)。

当前服务若已启动，不需要重复运行启动脚本。该脚本拒绝被占用的端口。日志在 `storage/traces/`。停止项目进程：

```powershell
.\scripts\stop.ps1
```

数据库沿用 `langgraph-postgres`，只读分析角色为 `agent_analytics_reader`。Redis 容器 `agent-analytics-redis` 使用本机端口 `6380`。十一张应用表与组件管理的 checkpoint 表已初始化，Navicat 刷新后可见。Milvus 复用 `milvus-standalone`，专用数据库为 `agent_analytics_memory`，原有 `default` 数据库保留。

分别在四个终端运行也可以，便于观察日志：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
python -m celery -A backend.workers.celery_app:celery_app worker --pool=solo --loglevel=info --queues celery --hostname analytics-manual@%h
python -m celery -A backend.workers.celery_app:celery_app worker --pool=solo --loglevel=info --queues analytics-memory --hostname memory-manual@%h
python -m celery -A backend.workers.celery_app:celery_app beat --loglevel=info --schedule storage/traces/celerybeat-schedule
```

Windows 使用多个独立 `solo` Worker 进程，后台 Python 包自动选择兼容 psycopg 的 Selector 事件循环。启动脚本默认两个 Worker，用 `-Workers 1` 可切回一个；上面的手动命令示例只启动一个 Worker。

## 配置真实模型

`.env` 与 `.env.example` 按十三组中文分类组织：原有十组之外，新增长期记忆与 Milvus、向量模型、历史摘要与上下文管理。每个字段注明用途和单位；修改后重启服务。

硅基流动配置如下（实际密钥请在本地文件查看和维护）：

```dotenv
MODEL_PROVIDER=siliconflow
MODEL_MODE=llm
MODEL_BASE_URL=https://api.siliconflow.cn/v1
MODEL_NAME=deepseek-ai/DeepSeek-V4-Flash
MODEL_API_KEY=你的密钥
MODEL_CONTEXT_TOKENS=32000
MODEL_MAX_TOKENS=1800
MODEL_TEMPERATURE=0.1
MODEL_ENABLE_THINKING=false
```

适配器在地址后追加 `/chat/completions`。`MODEL_CONTEXT_TOKENS` 是项目保守预算，不表示官方最大窗口。硅基流动的 `enable_thinking` 只发给该平台；开启思考时保留协议需要的 `reasoning_content`，不将其展示到事件或报告。

`MODEL_MODE` 控制页面初始选择和没有指定 `mode` 的 API 默认值；显式选择演示或真实模式仍可覆盖它。更换其他兼容平台时将 `MODEL_PROVIDER` 设为 `compatible`，然后修改地址、模型和密钥。

```powershell
# 一次真实工具调用测试，只发送固定测试文本，不发送业务数据。
python scripts/check_model.py
# 根据分类模板重排现有 .env，保留原有凭据和自定义值。
python scripts/reformat_env.py
```

## 导入数据

CSV 文件名采用 `customers.csv`、`products.csv`、`orders.csv`、`order_items.csv`，或官方 Olist 对应名称。XLSX 的四个工作表使用这些表名。源列可以不同，上传后在页面确认映射和口径。

```powershell
# 生成模拟模板，输出到 storage/uploads/demo-template。
python scripts/generate_demo_data.py
# 使用已创建的项目账号导入本地 Olist 文件，密码交互输入。
python scripts/import_olist.py --directory E:\path\to\olist --username 你的项目用户名
```

真实数据来源：[Olist 官方数据集](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)。内置示例明确标为模拟，不包含真实 Olist 文件。

## 测试与评测

```powershell
# 集成测试需 agent_analytics 数据库和 Docker 沙箱镜像。
python -m pytest -q
# API、Worker 和 Beat 运行后，使用自己的账号执行初始固定评测集。
python evaluations/run.py --username 你的项目用户名 --mode demo
# 自动创建独立评测账号，通过真实 HTTP、队列、数据库及沙箱执行完整实测。
python -m evaluations.benchmark --mode llm
# 自然问题、全量客户图表及零分母边界补测。
python -m evaluations.edge_benchmark --output evaluations/results/edges-new-run
# 针对已知缺陷再次检查真实工具与回答，不覆盖旧结果。
python -m evaluations.regression
# 真实模型五轮追问、后台记忆索引、摘要及 Milvus 语义召回。
python -m evaluations.memory_context
```

评测结果在 `evaluations/results/`。`evaluations/run.py` 保留原有五个模拟任务，用于初始回归；规则演示结果不代表真实模型效果。该脚本也可改为 `--mode llm`；当前定向复测证据见 [独立复核结果](evaluations/results/framework-upgrade-final-20261014/independent-review.json)。

完整评测使用 `cases/end_to_end.json` 的十一题及独立 Python/Decimal 基准，保存每题结果、事件、图表与报告；另有规模、重复和排队观测。集成测试运行前先停止本项目 Worker 和 Beat，结束后重新启动，避免 Beat 抢占测试任务。评测使用专用账号并保留取证数据，不保存密码、JWT 或平台 Key。

每个 Python 函数都有中文 docstring，前端每个函数也有中文注释。
