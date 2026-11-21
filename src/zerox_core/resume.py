"""断点续跑的纯函数（零运行期状态，可离线单测）。

只做一件事：从 logs/benchmark_metrics.jsonl 读出「已经跑完并落盘指标」的 bug，
给批量入口过滤掉，避免一次中断后重跑把前面 N 个完成的 bug 又烧一遍 token。

    read_completed  读 metrics，返回已完成 bug 的集合（按请求名，兼容带 b 的规范名）
    filter_pending  从待跑列表里剔除已完成的，返回 (待跑, 已跳过)
    state_from_events  从 work_log 事件重建续跑计数器（服务端/客户端共用同一口径）
    first_run_control  首轮 run_test 应注入的控制参数（新任务 force_checkout / 续跑 resume）

为什么只认 metrics、不认 work_log：
  - append_metric 只在 run_single_bug 正常返回后写，一行 = 这个 bug 跑完一轮（含 Failed/Error）；
  - 中途崩溃的 bug 不会写指标（crash.txt 那条路径会直接 raise），所以它不会被误判为完成；
  - 归档工具 new_run.py 会把整个 logs/ 移走，metrics 随之清空 —— 归档即干净的重启点，
    不需要额外状态文件。

只认终态里有明确结论的行：status 缺失或空的行不算完成（旧数据/半截写入要保守重跑）。

依赖方向：被 src/zerox.py 依赖；本模块只依赖标准库 json/os。
"""

import json
import os

# 这些 status 表示本次 bug 已有结论，不该因中断而重跑。
# Fixed / Failed 是 run_single_bug 的正常终态；Error 是兜底终态。
COMPLETED_STATUSES = frozenset({"Fixed", "Failed", "Error"})


def read_completed(metrics_path):
    """读指标文件，返回已完成 bug 的请求名集合。

    容错规则：
      - 文件不存在 → 空集合（首次运行）；
      - 坏行 / 非法 JSON → 跳过，不影响其它行（半截写入不能让整批失效）；
      - bug_id_requested 优先，回退 bug_id（历史行可能只有后者）；
      - 带 b 的规范名（Lang_1b）归一化为请求名（Lang_1），与 config 的 benchmark_bugs 对齐。
    """
    completed = set()
    if not metrics_path or not os.path.exists(metrics_path):
        return completed
    with open(metrics_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(row, dict):
                continue
            if row.get("status") not in COMPLETED_STATUSES:
                continue
            bug = row.get("bug_id_requested") or row.get("bug_id")
            if not isinstance(bug, str) or not bug:
                continue
            completed.add(_to_request_name(bug))
    return completed


def state_from_events(events, max_tests=None):
    """从 work_log 事件流重建一个未完成 bug 的续跑状态（纯函数，可离线单测）。

    这是 L1 续跑的核心：服务端的内存计数器（CURRENT_ROUND / TOTAL_ATTEMPTS /
    TOTAL_TESTS）和客户端的终态标志（saw_passing_test）进程重启后全部归零，
    但 work_log.jsonl 是 append-only 的权威事件流，可从它确定性重建。

    返回 dict:
      round           留存补丁的最大轮号（与 audit 的 rounds 同口径）；回滚已撤销的轮次不在 events 里
      attempts        补丁事件数（含已回滚前曾提交的次数？不含 —— 回滚会删 patch 事件）
      tests           test 事件数（含 Failed / Passed / Timeout 等所有已记录结果）
      saw_passing     是否见过 status=Passed 的 test（决定 agent 报 [FIXED] 是否被承认）
      at_limit        若传入 max_tests，tests >= max_tests 为真 —— 用于续跑后立即判定是否该终止

    容错：坏行 / 非 dict 事件由调用方 WorkLog.events() 过滤；这里只按 t 分类计数。
    轮号取 int，非整数轮号忽略（旧数据可能出现 None）。
    """
    round_no = 0
    attempts = 0
    tests = 0
    saw_passing = False
    for ev in events or ():
        t = ev.get("t")
        if t == "patch":
            attempts += 1
            r = ev.get("round")
            if isinstance(r, int) and r > round_no:
                round_no = r
        elif t == "test":
            tests += 1
            if ev.get("status") == "Passed":
                saw_passing = True
    state = {
        "round": round_no,
        "attempts": attempts,
        "tests": tests,
        "saw_passing": saw_passing,
    }
    if max_tests is not None:
        state["at_limit"] = tests >= max_tests
    return state


def first_run_control(resume):
    """返回首轮 run_test 客户端要注入的控制参数名（值恒为 True）。

    新任务注入 force_checkout —— 服务端全量 checkout + 清日志，任务边界由客户端划定；
    续跑注入 resume —— 服务端从 work_log 恢复进度，不 checkout、不删日志。

    之所以是纯函数而非内联三元：这个分支曾被写成 `fresh_task = not resume` +
    `if ... and fresh_task`，续跑时标志为 False 导致 resume 永远注入不进去，服务端
    遂把续跑当新任务清空 work_log。抽成函数后可离线单测锁住这条路径。
    """
    return "resume" if resume else "force_checkout"


def _to_request_name(bug):
    """规范名 Lang_1b → 请求名 Lang_1；其它形态原样保留。

    Defects4J 的规范名是「请求名 + b」，且请求名本身以数字结尾（Lang_1、Math_2），
    所以只在「以 b 结尾且倒数第二个字符是数字」时去掉 b —— 避免误伤本来就带 b 的 id。
    """
    if len(bug) >= 2 and bug.endswith("b") and bug[-2].isdigit():
        return bug[:-1]
    return bug


def filter_pending(requested, completed):
    """从待跑列表剔除已完成项，返回 (待跑列表, 已跳过列表)。

    保序、去重（按请求名），不改传入列表。匹配用 _to_request_name 归一化，
    所以 config 里写 Lang_1b、metrics 里记 Lang_1 也能对上。
    """
    done = {_to_request_name(b) for b in (completed or ())}
    pending, skipped = [], []
    seen = set()
    for bug in requested or ():
        key = _to_request_name(bug)
        if key in seen:
            continue
        seen.add(key)
        (skipped if key in done else pending).append(bug)
    return pending, skipped
