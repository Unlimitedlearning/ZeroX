"""worklog 自检：不开 WSL、不碰 Defects4J，只在临时目录里跑一遍完整生命周期。

    python tools/worklog_selftest.py

覆盖：apply（含新建文件）→ test 事件累加 → digest 渲染「好转/变差」→
      rollback 到第 1 轮（该轮新建的文件应被删除，改过的文件应回到 pristine）→
      重复 rollback 应无事发生。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
        assert wl.stats() == {"rounds": 2, "patches": 2, "tests": 2, "files": 2}, wl.stats()

        digest = wl.digest(limit=5, title="Demo_1b")
        assert "上轮 5" in digest and "好转 3" in digest, digest
        assert "变差" not in digest, digest

        restored, broken = wl.rollback(1)
        assert not broken, broken
        assert len(set(restored)) == 2, restored
        with open(target, encoding="utf-8", newline="") as f:
            assert f.read() == "line1\nline2\n", "应还原到 pristine"
        assert not os.path.exists(extra), "第 1 轮新建的文件应被回滚删除"
        assert wl.stats()["patches"] == 0, wl.stats()

        again, _ = wl.rollback(1)
        assert again == [], "没有可回滚的内容时应什么都不做"

        # 归档必须还在，供人工复盘
        with open(os.path.join(tmp, "logs", "human_audit.jsonl"), encoding="utf-8") as f:
            assert f.read().strip(), "被撤销的事件应归档到 human_audit.jsonl"

    print("worklog selftest: OK")


if __name__ == "__main__":
    main()
