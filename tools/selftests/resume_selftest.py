"""断点跳过纯函数自检：不连服务、不跑测试，只验 resume 的解析与过滤。

    python tools/selftests/resume_selftest.py

覆盖：
  1. 文件不存在 → 空集合
  2. 只认终态（Fixed/Failed/Error），非终态不算完成
  3. 坏行 / 空行 / 非 dict 行跳过，不影响其它行
  4. bug_id_requested 优先，回退 bug_id
  5. 规范名 Lang_1b 归一化为请求名 Lang_1
  6. 请求名本身以 b 结尾（Foo_b）不被误伤
  7. filter_pending 保序、去重、剔除已完成
  8. filter_pending 两边名字形态不同也能对上（config 写 b / metrics 不写）
  9. state_from_events 从事件流重建轮次/补丁/测试/Passed 标志
  10. state_from_events 只认存活 patch（回滚已删的事件不在流里）、忽略非整数轮号
  11. state_from_events at_limit 在 max_tests 边界上的判定
  12. first_run_control 新任务→force_checkout、续跑→resume（防续跑注入丢失回归）
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.resume import (  # noqa: E402
    read_completed, filter_pending, state_from_events, first_run_control,
)


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _write(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for row in rows:
            f.write((row if isinstance(row, str) else json.dumps(row, ensure_ascii=False)) + "\n")
    return path


def test_read_completed():
    _check(read_completed("/no/such/file.jsonl") == set(), "文件不存在应返回空集合")

    rows = [
        {"bug_id": "Lang_1", "bug_id_requested": "Lang_1", "status": "Fixed"},
        {"bug_id": "Codec_11b", "bug_id_requested": "Codec_11b", "status": "Failed"},
        {"bug_id": "Math_2", "status": "Error"},
        {"bug_id": "Chart_26", "status": "Running"},
        {"bug_id": "Time_14", "status": ""},
        {"status": "Fixed"},
        "not json {", "",
        [1, 2, 3],
    ]
    path = _write(rows)
    try:
        got = read_completed(path)
        _check(got == {"Lang_1", "Codec_11", "Math_2"}, f"完成集合不符: {got}")
    finally:
        os.unlink(path)


def test_filter_pending():
    completed = {"Lang_1", "Codec_11"}
    pending, skipped = filter_pending(
        ["Chart_26", "Lang_1", "Math_2", "Codec_11b", "Chart_26"], completed
    )
    _check(pending == ["Chart_26", "Math_2"], f"待跑列表不符: {pending}")
    _check(skipped == ["Lang_1", "Codec_11b"], f"跳过列表不符: {skipped}")

    # 形态不同也要对上：config 写规范名，metrics 记请求名
    pending, skipped = filter_pending(["Lang_1b", "Math_2"], {"Lang_1"})
    _check(pending == ["Math_2"] and skipped == ["Lang_1b"], "名字形态归一化失败")

    pending, skipped = filter_pending(["Foo_b"], {"Foo_b"})
    _check(pending == [] and skipped == ["Foo_b"], "以 b 结尾的请求名被误伤")

    pending, skipped = filter_pending([], completed)
    _check(pending == [] and skipped == [], "空输入应返回两个空列表")


def test_state_from_events():
    events = [
        {"t": "patch", "round": 1, "files": []},
        {"t": "test", "status": "Failed", "fail_count": 2},
        {"t": "patch", "round": 2, "files": []},
        {"t": "test", "status": "Failed", "fail_count": 1},
        {"t": "patch", "round": 3, "files": []},
        {"t": "test", "status": "Passed", "fail_count": 0},
        {"t": "rollback", "target_round": 3, "kind": "deadend"},
    ]
    st = state_from_events(events)
    _check(st["round"] == 3, f"轮次应取最大 patch round=3，实际 {st['round']}")
    _check(st["attempts"] == 3, f"补丁数=3，实际 {st['attempts']}")
    _check(st["tests"] == 3, f"测试事件数=3，实际 {st['tests']}")
    _check(st["saw_passing"] is True, "见过 Passed 应置位")

    # 回滚后的流里已无 round=3 的 patch（被删），只留 1、2：轮次应是 2
    survived = [e for e in events if not (e.get("t") == "patch" and e.get("round") == 3)]
    st2 = state_from_events(survived)
    _check(st2["round"] == 2 and st2["attempts"] == 2, f"回滚后轮次/补丁数应为 2/2: {st2}")

    # 非整数轮号忽略；空事件、None 都安全
    st3 = state_from_events([
        {"t": "patch", "round": None},
        {"t": "patch", "round": "4"},
        {"t": "test", "status": "Failed"},
    ])
    _check(st3["round"] == 0 and st3["attempts"] == 2 and st3["saw_passing"] is False,
           f"非整数轮号应忽略: {st3}")
    _check(state_from_events([])["round"] == 0, "空事件应返回零值")
    _check(state_from_events(None)["tests"] == 0, "None 事件应返回零值")


def test_state_at_limit():
    events = [{"t": "test", "status": "Failed"} for _ in range(3)]
    _check(state_from_events(events, max_tests=3)["at_limit"] is True, "测试数=上限应 at_limit")
    _check(state_from_events(events, max_tests=4)["at_limit"] is False, "测试数<上限不应 at_limit")
    _check("at_limit" not in state_from_events(events), "未传 max_tests 不应有 at_limit 字段")


def test_first_run_control():
    # 回归测试：续跑必须注入 resume，新任务必须注入 force_checkout。
    # 曾经因 `fresh_task = not resume` 使续跑首轮漏掉 resume，服务端清空 work_log。
    _check(first_run_control(False) == "force_checkout",
           f"新任务应注入 force_checkout，实际 {first_run_control(False)}")
    _check(first_run_control(True) == "resume",
           f"续跑应注入 resume，实际 {first_run_control(True)}")


def main():
    test_read_completed()
    test_filter_pending()
    test_state_from_events()
    test_state_at_limit()
    test_first_run_control()
    print("resume selftest: OK")


if __name__ == "__main__":
    main()
