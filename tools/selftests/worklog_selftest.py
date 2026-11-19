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
        with open(os.path.join(tmp, "logs", "human_audit.jsonl"), encoding="utf-8") as f:
            assert f.read().strip(), "被撤销的事件应归档到 human_audit.jsonl"

    print("worklog selftest: OK")


if __name__ == "__main__":
    main()
