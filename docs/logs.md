# 运行记录与指标

每个Bug在 `logs/<bug>/` 下产生四类文件：`work_log.jsonl`（工作日志）、`blobs/`（补丁前后内容的存储）、`test.log`（最近一次测试的原始输出）与 `transcript.md`（执行记录）。本页说明对外报告所依赖的两种文件；工作日志的格式见 README 的「设计取舍」一节。

## 指标文件 `logs/benchmark_metrics.jsonl`

每个已结束的Bug一行 JSON，由 `src/zerox.py`（客户端）追加写入，含崩溃与超时的运行。token 用量仅对调用模型的一方可见，故该记录无法由服务端生成。

| 字段 | 含义 |
| :--- | :--- |
| `bug_id` / `bug_id_requested` | `benchmark_bugs` 中的标识符（如 `Lang_1`）；磁盘目录用规范名（`Lang_1b`），`audit.py` 自行映射 |
| `status` | `Fixed` / `Failed` / `Error`。`MaxTurns` 已随 `agent.max_turns` 移除，收敛条件为测试上限 |
| `total_time_seconds` | 端到端墙钟时间 |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | 该Bug全部模型调用的用量之和 |
| `api_calls` / `turns` | 模型请求次数 / 对话轮数 |
| `tests` / `total_attempts` / `current_round` | 测试次数（上限单位）、`patch_and_log` **调用次数**（被拒亦计数）、最高轮次 |
| `audit` / `audit_violations` | 事后判定 `clean` / `invalid` 及导致 `invalid` 的路径 |
| `audit_version` | 审计规则版本（`audit.AUDIT_VERSION`），规则变更时递增以保证旧记录可识别 |

## 执行记录 `logs/<bug>/transcript.md`

记录agent消息（`AI`）、模型思考（`think`）、工具调用及**实际参数**（`call:run_test {…}`）、响应（`result:…`，截断至 2000 字符并注明原长）以及压缩丢弃字符数的 `compact` 标记，每个Bug开始时重写。

压缩会从对话历史中删除信息，但不会从记录中删除，故运行结束后可在不重放上下文的情况下查阅当时的推理。该文件**不作为决策输入**：压缩由 `agent.compact_context_chars` 触发，与其大小无关。

## 版本控制范围

`logs/` 下 git 仅跟踪 `logs/benchmark_metrics.jsonl`、`logs/audit.jsonl` 与 `logs/<bug>/human_audit.jsonl`（被回滚的补丁与丢弃轨迹），使已发布的数字可审计，同时避免提交全部临时日志。其余文件与 `logs/archive/` 均被忽略。
