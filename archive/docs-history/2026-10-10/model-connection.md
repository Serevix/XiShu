# 硅基流动模型接入与配置分类

日期：2026-10-09。

## 当前接入

- 平台：硅基流动。
- 接口前缀：`https://api.siliconflow.cn/v1`。
- 模型：`deepseek-ai/DeepSeek-V4-Flash`。
- 默认运行模式：`llm`，页面初始选择真实模型；请求显式传 `demo` 仍可运行规则演示。
- 密钥：只写入本地 `.env`，不记录到本文、模板、源码或验证输出。
- 默认关闭深度思考；开启时适配器保留必要的 `reasoning_content` 协议字段，不将它展示到事件和报告。

官方参数参考：[硅基流动 Chat Completions](https://docs.siliconflow.cn/docs/api/chat-completions-post)。

## .env 分类

| 分组 | 配置 | 用途 |
| --- | --- | --- |
| PostgreSQL | `DATABASE_URL`、`SQL_READER_URL` | 写入账号与受限分析账号 |
| 登录认证 | `JWT_SECRET` | 登录令牌签名，与模型 Key 分开 |
| 任务队列 | `REDIS_URL` | Celery 消息队列 |
| 模型连接 | `MODEL_PROVIDER`、`MODEL_MODE`、`MODEL_BASE_URL`、`MODEL_NAME`、`MODEL_API_KEY` | 平台、默认模式、地址、模型与鉴权 |
| 生成参数 | `MODEL_CONTEXT_TOKENS`、`MODEL_MAX_TOKENS`、`MODEL_TEMPERATURE`、`MODEL_ENABLE_THINKING`、`MODEL_TIMEOUT_SECONDS` | 输入预算、输出上限、随机性、思考及请求时限 |
| 文件存储 | `STORAGE_ROOT` | 原文件与分析产物 |
| Python 沙箱 | `SANDBOX_IMAGE` | 分析执行容器镜像 |
| Agent 限制 | `MAX_AGENT_STEPS`、`TASK_TIMEOUT_SECONDS` | 步数与整个任务时限 |
| 上传限制 | `MAX_UPLOAD_MB` | 上传总大小，单位 MiB |
| SQL 限制 | `MAX_SQL_ROWS`、`SQL_TIMEOUT_MS` | 输出行数与查询时限，后者单位毫秒 |

每组和每个字段均有中文注释。`.env.example` 使用相同分类，但不包含实际凭据。原有数据库、JWT 和 Redis 值保留；已有自定义项保留在额外分组。

`MODEL_CONTEXT_TOKENS=32000` 是本项目主动采用的保守预算，不表示官方最大上下文窗口。`MODEL_MODE` 现在实际控制默认模式，生成温度与输出上限也由配置读取。

## 实际验证

首次执行 `python scripts/check_model.py`：成功返回 `get_dataset_schema` 工具调用，供应商返回总 Token 用量 758。测试仅发送固定文本和工具定义。

随后通过实际 API、Redis Worker、PostgreSQL 和 Docker 沙箱运行一次真实模型分析任务。任务成功完成，包含数据字典、SQL、图表、Python 金额占比及报告产物；报告检查拒绝过一次说明文字中的数字，模型读取反馈后修正。

三个品类金额分别为 2970.00、2200.00、1380.00 BRL，合计 6550.00 BRL，与模拟数据标准结果一致。分组订单数不是互斥分组，不能相加当作整体订单数。

该分析的供应商统计为输入 Token 14119、输出 Token 1103、总计 15222。Token 用量不等于费用，本文不估算账单。

这只是一项固定模拟数据上的真实调用验证，不代表完整 Olist、所有问题或大规模真实模型评测均已通过。

接入修改后完整回归：`33 passed`，耗时 25.98 秒。配置保留、供应商参数、思考字段回传、默认模式及原有核心流程均已检查。

## 配置变更与检查

```powershell
# 修改 .env 后重启项目自己的进程。
.\scripts\stop.ps1
.\scripts\start.ps1

# 小规模模型工具协议检查。
python scripts/check_model.py

# 按分类模板整理本地配置，保留已填写值。
python scripts/reformat_env.py
```
