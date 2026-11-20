"""worklog 自检：不开 WSL、不碰 Defects4J，只在临时目录里跑一遍完整生命周期。

    python tools/selftests/worklog_selftest.py

覆盖：apply（含新建文件）→ test 事件累加 → digest 渲染「好转/变差」→
      rollback 到第 1 轮（该轮新建的文件应被删除，改过的文件应回到 pristine）→
      重复 rollback 应无事发生。
"""
import os
import sys
import tempfile

# tools/selftests/ 比 tools/ 深一层，所以要比原来多剥一次 dirname 才能拿到项目根目录；
# 代码在 src/ 下面，两个路径都要在 sys.path 里
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import worklog  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        target = os.path.join(src, "Foo.java")
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write("line1\nline2\n")
        extra = os.path.join(src, "Hex.java")

        wl = worklog.WorkLog(os.path.join(tmp, "logs"))

        # 第 1 轮：改 Foo + 新建 Hex
        wl.apply(1, "PATCH_GENERATED", "边界判断错了", "把 <= 改成 <", [
            (target, "line1\nline2\n", "line1\nline2\nline3\n"),
            (extra, None, "class Hex {}\n"),
        ])
        wl.test(1, "Failed", 5, "5 failing tests ...")

        # 第 2 轮：再改一次 Foo，失败数从 5 降到 2
        wl.apply(2, "PATCH_GENERATED", "换个方向", "重写查找", [
            (target, "line1\nline2\nline3\n", "line1\nlineX\nline3\n"),
        ])
        wl.test(2, "Failed", 2, "2 failing tests ...")

        assert os.path.exists(extra), "新建文件应当真的写进去了"
        assert wl.stats() == {"rounds": 2, "patches": 2, "tests": 2, "files": 2,
                              "rollbacks": 0, "probe_rollbacks": 0}, wl.stats()

        digest = wl.digest(limit=5, title="Demo_1b")
        assert "上轮 5" in digest and "好转 3" in digest, digest
        assert "变差" not in digest, digest
        # 压缩后要靠 reasoning 找回「当时为什么改」，不能只看到改了哪些行
        assert "边界判断错了" in digest and "换个方向" in digest, digest
        assert "当前推断（轮 2）" in digest, digest

        # 【滚动摘要】brief() 是压缩时常驻注入的精简视图：含当前推断与失败趋势，且受长度约束
        brief = wl.brief(title="Demo_1b")
        assert "当前推断（轮 2）" in brief and "换个方向" in brief, brief
        assert "测试趋势：5 → 2" in brief, brief
        assert len(wl.brief(max_chars=80)) <= 90, "brief 必须尊重 max_chars 上限"
        assert "暂无历史工作日志" in worklog.WorkLog(os.path.join(tmp, "empty")).brief()

        # 【think 原文】压缩时回传的最近三条：独立文件、只增不删、brief 里给节选
        wl.note(1, "假设一：边界判断错了，先查 >= 与 >")
        wl.note(2, "假设二：换个方向，从测试方法的 expected 反推")
        assert [e["turn"] for e in wl.thoughts()] == [1, 2], wl.thoughts()
        assert [e["turn"] for e in wl.thoughts(limit=1)] == [2], "只取最近一条"
        assert os.path.exists(os.path.join(tmp, "logs", "thoughts.jsonl")), "think 应单独落盘"
        b_think = wl.brief(title="Demo_1b")
        assert "最近思考" in b_think and "假设二" in b_think, b_think
        assert "当前推断（轮 2）" in b_think, b_think
        assert len(wl.brief(max_chars=120)) <= 130, "think 节选也要受 max_chars 约束"
        assert wl.note(3, "   ") is None, "空 think 不应落盘"

        # 【未验证补丁】打完补丁还没跑测试时，状态卡不能让旧结果看起来像新结果：
        # Lang_10 上模型把单次测试的「2 → 2」读成「刚打的补丁仍失败」，转去写探针，多花了两倍 token。
        tmp2 = os.path.join(tmp, "case2")
        os.makedirs(tmp2)
        target2 = os.path.join(tmp2, "Bar.java")
        with open(target2, "w", encoding="utf-8", newline="") as f:
            f.write("a\nb\n")
        wl2 = worklog.WorkLog(os.path.join(tmp, "logs2"))
        wl2.test(0, "Failed", 2, "2 failing tests ...")
        b1 = wl2.brief(title="Demo_2b")
        assert "→" not in b1 and "上次测试（轮 0）" in b1, b1
        wl2.apply(1, "PATCHED", "空格应精确匹配", "把 \\s*+ 改成 \\s", [
            (target2, "a\nb\n", "a\nc\n"),
        ])
        b2 = wl2.brief(title="Demo_2b")
        assert "未验证：轮 1" in b2, b2
        wl2.test(1, "Passed", 0, "")
        b3 = wl2.brief(title="Demo_2b")
        assert "未验证" not in b3 and "测试趋势：2 → 0" in b3, b3
        wl2.apply(2, "PATCHED", "再改一次", "调整边界", [(target2, "a\nc\n", "a\nd\n")])
        b4 = wl2.brief(title="Demo_2b")
        assert "测试趋势：2 → 0" in b4 and "未验证：轮 2" in b4, b4

        restored, broken = wl.rollback(1, reasoning="探针确认 indexOfLast 返回首个匹配", kind="probe")
        assert not broken, broken
        assert len(set(restored)) == 2, restored
        with open(target, encoding="utf-8", newline="") as f:
            assert f.read() == "line1\nline2\n", "应还原到 pristine"
        assert not os.path.exists(extra), "第 1 轮新建的文件应被回滚删除"
        assert wl.stats()["patches"] == 0, wl.stats()

        # 回滚记录必须活过重写：被撤销的轮次只在 human_audit.jsonl 里，agent 看不到
        rb_events = [ev for ev in wl.events() if ev["t"] == "rollback"]
        assert len(rb_events) == 1, rb_events
        assert rb_events[0]["kind"] == "probe" and rb_events[0]["target_round"] == 1, rb_events[0]
        assert wl.stats()["rollbacks"] == 1 and wl.stats()["probe_rollbacks"] == 1, wl.stats()
        assert [e["turn"] for e in wl.thoughts()] == [1, 2], "回滚重写 work_log 不应删掉 think 原文"
        digest_after = wl.digest(limit=5, title="Demo_1b")
        assert "[回滚]" in digest_after and "撤销到第 1 轮（探针）" in digest_after, digest_after
        # 压缩后的摘要必须带上「已排除方向」，否则模型会重复走死路
        brief_after = wl.brief(title="Demo_1b")
        assert "已排除" in brief_after and "探针" in brief_after, brief_after

        again, _ = wl.rollback(1, reasoning="重复调用不应再出记录", kind="deadend")
        assert again == [], "没有可回滚的内容时应什么都不做"
        assert wl.stats()["rollbacks"] == 1, "空回滚不应往日志里添记录"

        # 再回滚一次更低的 target，早先那条回滚记录必须还在（它不带 round，不会被 kept 过滤掉）
        wl.apply(1, "PATCH_GENERATED", "换个方向", "重写查找", [
            (target, "line1\nline2\n", "line1\nlineY\n"),
        ])
        wl.rollback(1, reasoning="<= 写法还是不对", kind="deadend")
        assert wl.stats()["rollbacks"] == 2, wl.stats()
        assert wl.stats()["probe_rollbacks"] == 1, "更早的探针回滚记录不应被第二次回滚删掉"

        # 归档必须还在，供人工复盘
        audit_path = os.path.join(tmp, "logs", "human_audit.jsonl")
        with open(audit_path, encoding="utf-8") as f:
            assert f.read().strip(), "被撤销的事件应归档到 human_audit.jsonl"

        # ---- 开跑重置：清事件日志，保留审计归档 ----
        assert os.path.exists(wl.log_path), "重置前应有事件日志"
        wl.reset_events()
        assert not os.path.exists(wl.log_path), "reset_events 应删除 work_log.jsonl"
        assert wl.events() == [], "重置后不应读到任何历史事件"
        assert "暂无历史工作日志" in wl.brief(), "重置后状态卡应从空白开始"
        assert os.path.exists(audit_path), "reset_events 不能删 human_audit.jsonl（事后审计）"
        # 重复调用不应报错（首次 run_test 前服务端 checkout 也会再删一次）
        wl.reset_events()

    print("worklog selftest: OK")


if __name__ == "__main__":
    main()
