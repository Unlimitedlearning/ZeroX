"""audit.py 的离线自检：不连 WSL、不跑测试、不碰任何工作区。

只验证判定逻辑：
  1. 只改生产代码        -> clean
  2. 改测试代码          -> invalid，且能说出是哪一轮哪一个文件
  3. 新建测试文件        -> invalid + created=true（还原时要 rm 而不是 git checkout）
  4. bug 名解析          -> Lang_1 命中 Lang_1b，不会错抓到 Lang_10b
  5. 前缀命中多个目录    -> 报错而不是猜
  6. 没有日志            -> unknown（不能算成 clean）

跑法：python tools/audit_selftest.py
期望输出最后一行：audit selftest: OK
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit  # noqa: E402


SRC = "/ws/d4j_Lang_1b/src/main/java/org/apache/commons/lang3/math/NumberUtils.java"
TEST = "/ws/d4j_Lang_1b/src/test/java/org/apache/commons/lang3/math/NumberUtilsTest.java"
NEW_TEST = "/ws/d4j_Lang_1b/src/test/java/org/apache/commons/lang3/math/ExtraTest.java"

GLOBS = ["*/src/test/*", "*/tests/*", "*/test/*"]


def event(kind, **kwargs):
    payload = {"t": kind}
    payload.update(kwargs)
    return payload


def write_log(bug_dir, events):
    os.makedirs(bug_dir, exist_ok=True)
    with open(os.path.join(bug_dir, "work_log.jsonl"), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    tmp = tempfile.mkdtemp(prefix="zerox_audit_")
    try:
        # ---- 1. 只改生产代码 -> clean ----
        clean_dir = os.path.join(tmp, "cleanb")
        write_log(clean_dir, [
            event("patch", round=1, files=[{"path": SRC, "before": "sha1", "after": "sha2"}]),
            event("test", round=1, status="Failed", fail_count=5),
            event("patch", round=2, files=[{"path": SRC, "before": "sha2", "after": "sha3"}]),
            event("test", round=2, status="Passed", fail_count=0),
        ])
        verdict = audit.audit_bug_dir(clean_dir, test_globs=GLOBS)
        check(verdict["audit"] == "clean", f"期望 clean，得到 {verdict['audit']}")
        check(verdict["patches"] == 2, f"期望 2 个补丁，得到 {verdict['patches']}")
        check(verdict["tests"] == 2, f"期望 2 次测试，得到 {verdict['tests']}")
        check(verdict["rounds"] == 2, f"期望 round=2，得到 {verdict['rounds']}")
        check(verdict["last_test_status"] == "Passed", "最后一条测试状态没跟上")

        # ---- 2. 改了测试代码 -> invalid ----
        dirty_dir = os.path.join(tmp, "dirtyb")
        write_log(dirty_dir, [
            event("patch", round=1, files=[{"path": SRC, "before": "sha1", "after": "sha2"}]),
            event("patch", round=2, files=[{"path": TEST, "before": "sha3", "after": "sha4"}]),
            event("test", round=2, status="Passed", fail_count=0),
        ])
        verdict = audit.audit_bug_dir(dirty_dir, test_globs=GLOBS)
        check(verdict["audit"] == "invalid", f"期望 invalid，得到 {verdict['audit']}")
        check(len(verdict["violations"]) == 1, "违规文件数量不对")
        check(verdict["violations"][0]["path"] == TEST, "违规文件认错了")
        check(verdict["violations"][0]["rounds"] == [2], "违规轮次不对")
        check(verdict["violations"][0]["created"] is False, "改已有文件不该标成 created")

        # ---- 3. 新建测试文件 -> invalid + created ----
        created_dir = os.path.join(tmp, "createdb")
        write_log(created_dir, [
            event("patch", round=1, files=[{"path": NEW_TEST, "before": None, "after": "sha9"}]),
        ])
        verdict = audit.audit_bug_dir(created_dir, test_globs=GLOBS)
        check(verdict["audit"] == "invalid", "新建测试文件必须判 invalid")
        check(verdict["violations"][0]["created"] is True, "新建文件必须标 created=true")

        # ---- 4. bug 名解析：Lang_1 命中 Lang_1b，不能抓到 Lang_10b ----
        logs_dir = os.path.join(tmp, "logs")
        write_log(os.path.join(logs_dir, "Lang_1b"), [event("test", round=0, status="Failed")])
        write_log(os.path.join(logs_dir, "Lang_10b"), [event("test", round=0, status="Failed")])
        found = audit.find_bug_dir(logs_dir, "Lang_1")
        check(found and os.path.basename(found) == "Lang_1b",
              f"Lang_1 应命中 Lang_1b，实际：{found}")

        # ---- 5. 前缀命中多个目录 -> 报错而不是猜 ----
        write_log(os.path.join(logs_dir, "Lang_1f"), [event("test", round=0, status="Failed")])
        try:
            audit.find_bug_dir(logs_dir, "Lang_1")
        except ValueError:
            pass
        else:
            raise AssertionError("Lang_1b / Lang_1f 同时存在时必须报错，不能猜一个")

        # ---- 6. 没有日志 -> unknown ----
        verdict = audit.audit_bug(logs_dir, "Csv_99", test_globs=GLOBS)
        check(verdict["audit"] == "unknown", f"缺日志必须是 unknown，得到 {verdict['audit']}")

        # ---- 7. 空事件流也不炸 ----
        check(audit.audit_events([])["audit"] == "clean", "空 work_log 要能跑完")

        # ---- 8. 显式空 globs（分不出测试代码）→ 一律 clean，不能回退到兜底 globs ----
        verdict = audit.audit_bug_dir(dirty_dir, test_globs=[])
        check(verdict["audit"] == "clean", "明显空 globs 时不应把测试文件判成违规")

        print("audit selftest: OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
