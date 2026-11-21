# 中断续跑

长时间批量运行时，进程可能被手动中断或卡死。ZeroX 提供两级恢复，都不重放对话历史 —— 进度以 `work_log.jsonl` 为权威来源，靠每轮注入的 L1 状态卡续接。

## L0：断点跳过（批量级）

```bash
python src/zerox.py --skip-done
```

读 `benchmark_metrics.jsonl`，把已落指标（终态 `Fixed` / `Failed` / `Error`）的 bug 从待跑列表剔除，只跑没跑完的那几个。适用于「一批 10 个跑到第 7 个中断，前 6 个已落盘」的情形。

- 是显式开关，默认全跑 —— 复测同一批时不会被悄悄跳过。
- 若全部已完成，只打印一句「请先归档：`python tools/new_run.py`」后退出，不启动服务端。
- 中断在测试中途、尚未写指标的 bug 不会被误判为完成（指标只在 `run_single_bug` 正常返回后追加）。

## L1：bug 内续跑（单 bug 级）

```bash
python src/zerox.py --resume-bug Math_2
```

只续这一个 bug，从它的 `logs/Math_2b/work_log.jsonl` 恢复轮次、补丁数、测试数与「是否见过通过的测试」，工作区保持中断时的原样，不重新 checkout、不清日志。适用于「单个 bug 跑到第 8 轮卡死，不想从头再来」。

与 `--skip-done` 互斥（同时传会忽略后者）。续跑段在 `benchmark_metrics.jsonl` 追加一行并标记 `"resumed": true`；统计同一 bug 时按 `bug_id` 取最后一行 —— token 与耗时只计续跑段，中断前的用量无法从日志反推。

### 恢复机制

- **进度**：`resume.state_from_events()` 把 `work_log` 事件流重建为计数器；服务端据此恢复 `CURRENT_ROUND` / `TOTAL_ATTEMPTS` / `TOTAL_TESTS`，客户端恢复同名 counters 与 `saw_passing_test`。
- **上下文**：`messages` 全新，不序列化对话。首轮请求末尾注入由 `work_log.brief()` 重建的状态卡，加上 `thoughts.jsonl` 最近 3 条 think 原文 —— 与压缩后的恢复走同一条路径。
- **首轮 run_test**：传 `resume=true`（而非全新任务的 `force_checkout=true`），服务端走 `resume_from_worklog()` 分支，不删日志、不 checkout。

### 前提：先人工检查工作区（重要）

**服务端不自动校验工作区与日志是否一致，续跑前必须人工确认。** 这一步刻意不做成自动关卡：自动 hash 校验既慢又可能误拒绝，而续跑是容错兜底、不是实验主路径。

检查要点：

1. **工作区源码**是否是中断时的预期状态 —— 若中断恰好落在 `rollback` 写一半（倒序逐文件还原 + 重写日志，非原子），工作区可能已部分还原但日志未更新。
2. 核对 `logs/<bug>/work_log.jsonl` 最后一条 `patch` 的文件与当前源码是否对得上；对不上就别续跑，改用归档重跑。
3. 若历史测试次数已达 `agent.max_tests`，续跑首轮 `run_test` 会直接判失败 —— 此时应归档后重跑，而非续跑。

研究场景下**重跑通常优于续跑**：重跑是干净对照，续跑混入了中断时的中间状态。L1 主要面向真实 long-running 任务的容错。
