# 运行记录与指标

每个Bug在 `logs/<bug>/` 下产生六类文件：`work_log.jsonl`（工作日志）、`blobs/`（补丁前后内容的存储）、`test.log`（最近一次测试的原始输出）、`transcript.md`（执行记录）、`thoughts.jsonl`（模型思考原文）与 `l1_state.jsonl`（每轮注入的状态卡）。本页说明对外报告所依赖的文件；工作日志的格式见 README 的「设计取舍」一节。

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

## 思考原文 `logs/<bug>/thoughts.jsonl`

客户端每轮从模型响应里取一次 think 原文（provider 字段名可能是 `reasoning_content` 或 `reasoning`），追加一行 `{"t":"thought","ts":...,"turn":N,"text":"..."}`。空思考不落盘。它独立于 `work_log.jsonl`：`rollback()` 只重写工作日志，不删除这里的历史，因为被撤销方向的思考正是压缩后需要保留的线索。

压缩发生时，客户端取最近 3 条原文放进保留下来的提示里回传模型（单条超过 1600 字则在提示中截断并标明原长，完整内容始终在本文件）；状态卡 `brief()` 另给每条 180 字的节选。这两条通道解决的是同一问题：补丁形成前的假设只存在于 think 中，压缩若把它们丢掉，模型会在空状态上重新推导（Chart_13 实测）。

## 状态卡快照 `logs/<bug>/l1_state.jsonl`

每次成功调用模型后追加一行 `{"ts":...,"turn":N,"compacted":bool,"chars":N,"content":"...","notice":"..."}`：`content` 是**该轮实际注入**的 L1 状态卡原文；`notice` 是本轮保留的压缩提示原文（含回传的 think，未压缩时为空串）。二者合起来就是模型在恢复时收到的全部信息。transcript 只记录「发生了一次压缩」，看不到模型在压缩后到底收到了什么；本文件使这件事可核对。

状态卡拼接在请求末尾、不进入 `messages` 正文，故压缩裁剪不会碰到它，也不计入 `context_chars`；每轮刷新意味着模型每一轮都带着最新状态，恢复不依赖它主动调用 `get_work_log`。

## 版本控制范围

`logs/` 下 git 仅跟踪 `logs/benchmark_metrics.jsonl`、`logs/audit.jsonl` 与 `logs/<bug>/human_audit.jsonl`（被回滚的补丁与丢弃轨迹），使已发布的数字可审计，同时避免提交全部临时日志。其余文件与 `logs/archive/` 均被忽略。
