"""search_code 纯逻辑自检：grep 命令构造、定义优先排序、结果整理、声明范围标注；不连 WSL。

    python tools/selftests/search_code_selftest.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.search_code import (  # noqa: E402
    MAX_RANGE_FILES, SCAN_LIMIT, build_grep_command, definition_score, format_results, parse_hits,
)


def check(cond, message):
    if not cond:
        raise AssertionError(message)


WORK = "/home/user/zerox_workdir/d4j_Time_14b"


def test_command():
    cmd, err = build_grep_command(WORK, "*.java", "public int[] add(ReadablePartial")
    check(err is None, err)
    check("grep -rn -F " in cmd, f"应默认字面匹配: {cmd}")
    check("public int[] add(ReadablePartial" in cmd, f"元字符应原样保留: {cmd}")
    check("--include='*.java'" in cmd, f"include 需加引号: {cmd}")
    check(f"head -n {SCAN_LIMIT}" in cmd, f"应有扫描上限: {cmd}")

    cmd_re, err = build_grep_command(WORK, "*.java", "getOffset.*Local", regex=True)
    check(err is None and "grep -rn -E " in cmd_re, f"regex=true 应用 -E: {cmd_re}")

    cmd_dash, err = build_grep_command(WORK, "*.java", "-field")
    check(err is None and " -- -field " in cmd_dash, f"'-' 开头须用 -- 隔离: {cmd_dash}")

    cmd_bad, err = build_grep_command(WORK, "*.java", "   ")
    check(cmd_bad is None and "keyword" in err, "空 keyword 应拒绝")


def test_scoring():
    check(definition_score("public final class MonthDay") == 3, "类型定义应最高")
    check(definition_score("    public void testPlusMonths() {") == 2, "方法定义应次之")
    check(definition_score("private static final int SAFE = 76;") == 1, "字段定义应再次")
    check(definition_score("        foo.add(value);") == 0, "普通引用应为 0")


def test_format():
    stdout = "\n".join([
        f"{WORK}/src/main/java/Foo.java:12:        Foo f = new Foo();",
        f"{WORK}/src/test/java/FooTest.java:30:    public void testFoo() {{",
        f"{WORK}/src/main/java/Foo.java:71:public final class Foo",
        f"{WORK}/src/main/java/Foo.java:bad:not a line",
    ])
    text = format_results(stdout, "Foo", WORK, "*.java")
    lines = text.splitlines()
    check("扫描到 3 条命中" in lines[0], lines[0])
    check("字面匹配" in lines[0], lines[0])
    check(len(lines) == 4, f"畸形行应跳过，得 {len(lines)} 行")
    check("src/main/java/Foo.java:71:public final class Foo" == lines[1], lines[1])
    check("src/test/java/FooTest.java:30:" in lines[2], lines[2])
    check("src/main/java/Foo.java:12:" in lines[3], lines[3])
    check(WORK not in text, "路径应为工作区相对路径")

    clipped = format_results(stdout, "Foo", WORK, "*.java", max_results=1)
    check("显示 1 条" in clipped and "其余 2 条" in clipped, clipped)

    empty = format_results("", "absent", WORK, "*.java", regex=True)
    check("扫描到 0 条命中" in empty and "正则匹配" in empty, empty)


SOURCE = "\n".join([
    "package p;",                        # 1
    "",                                  # 2
    "public final class Foo {",          # 3
    "    private int n;",                # 4
    "",                                  # 5
    "    public Foo(int n) {",           # 6
    "        this.n = n;",               # 7
    "    }",                             # 8
    "",                                  # 9
    "    public int add(int a) {",       # 10
    "        if (a > n) {",              # 11
    "            return a;",             # 12
    "        }",                         # 13
    "        return n + a;",             # 14
    "    }",                             # 15
    "}",                                 # 16
])
FOO = "src/main/java/Foo.java"


def test_ranges():
    stdout = "\n".join([
        f"{WORK}/{FOO}:10:    public int add(int a) {{",
        f"{WORK}/{FOO}:12:            return a;",
        f"{WORK}/{FOO}:3:public final class Foo {{",
        f"{WORK}/{FOO}:6:    public Foo(int n) {{",
        f"{WORK}/src/main/java/Zed.java:7:    public void run() {{",
    ])
    asked = []

    def loader(rels):
        asked.append(list(rels))
        return {FOO: SOURCE}  # Zed.java 回读失败

    text = format_results(stdout, "a", WORK, "*.java", source_loader=loader)
    lines = text.splitlines()
    check("声明" in lines[0] and "[起-止]" in lines[0], f"表头应说明标注含义: {lines[0]}")
    check(len(lines) == 6, f"应保留全部 5 条命中，得 {len(lines)} 行")
    # 排序：类(3) > 方法/构造(2) > 引用(0)
    check(lines[1].endswith("public final class Foo {"), f"类声明不做标注: {lines[1]}")
    check(lines[2].endswith("[6-8]"), f"构造声明应附构造体范围: {lines[2]}")
    check(lines[3].endswith("[10-15]"), f"方法声明应附方法体范围: {lines[3]}")
    check(not lines[4].endswith("]"), f"回读失败的文件不应标注: {lines[4]}")
    check(not lines[5].endswith("]"), f"普通引用不应标注: {lines[5]}")
    check(asked == [[FOO, "src/main/java/Zed.java"]], f"应一次问回全部文件: {asked}")

    # 无 loader / loader 抛错 / 回读为空：退化成不带标注的原始格式，不影响结果
    plain = format_results(stdout, "a", WORK, "*.java")
    check("[起-止]" not in plain.splitlines()[0], "无 loader 时不应声称有标注")
    check("[10-15]" not in plain, plain)
    safe = format_results(stdout, "a", WORK, "*.java",
                          source_loader=lambda rels: (_ for _ in ()).throw(RuntimeError("x")))
    check(safe == plain, "loader 抛错应与无 loader 等价")
    check(format_results(stdout, "a", WORK, "*.java", source_loader=lambda rels: {}) == plain,
          "回读为空应与无 loader 等价")

    # 文件数超过上限时只问前 MAX_RANGE_FILES 个，且顺序稳定
    many = "\n".join(f"{WORK}/src/F{i}.java:{10 + i}:    public void f{i}() {{" for i in range(9))
    asked.clear()
    format_results(many, "f", WORK, "*.java", source_loader=loader)
    check(len(asked[0]) == MAX_RANGE_FILES, f"应按上限截断回读文件数: {asked}")

    check(len(parse_hits(stdout, WORK)) == 5, "parse_hits 应与 format_results 口径一致")


if __name__ == "__main__":
    test_command()
    test_scoring()
    test_format()
    test_ranges()
    print("search_code selftest: OK")
