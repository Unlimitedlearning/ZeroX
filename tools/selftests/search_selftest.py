"""search_code 纯逻辑自检：不连 WSL、不跑 grep，只验命令构造与结果整理。

    python tools/selftests/search_selftest.py

覆盖：
  1. 默认字面匹配（-F），含 [] () 的 Java 声明不再被当成正则
  2. regex=true 切到 -E；keyword 以 '-' 开头不会逃逸成 grep 选项
  3. 空 keyword 明确拒绝，而不是 grep 全文件匹配
  4. 结果按定义优先排序、路径相对工作区、畸形行跳过
  5. max_results 截断有总数与剩余数提示；regex 模式在摘要里标明
  6. extract_method_body 按花括号配平只取一个方法，字符串/注释里的括号不干扰
  7. extract_compile_errors 提取去重、路径压 basename，提取不到时回退
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.search import (  # noqa: E402
    SCAN_LIMIT,
    build_grep_command,
    definition_score,
    extract_compile_errors,
    extract_method_body,
    format_results,
    locate_method_line,
    parse_failing_tests,
)

WORK = "/home/user/zerox_workdir/d4j_Time_14b"


def check(cond, message):
    if not cond:
        raise AssertionError(message)


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


def test_failing_tests():
    detail = (
        "  - org.apache.commons.codec.net.QuotedPrintableCodecTest::testSkipNotEncodedCRLF\n"
        "  - org.apache.commons.codec.net.QuotedPrintableCodecTest::testTrailingSpecial\n"
        "  - org.apache.commons.codec.net.QuotedPrintableCodecTest::testTrailingSpecial\n"
        "  - plain text without a method\n"
    )
    pairs = parse_failing_tests(detail)
    check(len(pairs) == 2 and pairs[0] == (
        "org.apache.commons.codec.net.QuotedPrintableCodecTest", "testSkipNotEncodedCRLF"
    ), f"应提取且去重: {pairs}")
    check(parse_failing_tests("no test refs here") == [], "无类名时不猜")

    source = "\n".join([
        "public class FooTest {",
        "    public void setUp() {",
        "    }",
        "    public void testTrailingSpecial() {",
        "        assertEquals(1, 2);",
        "    }",
        "    // public void testTrailingSpecial() commented",
        "}",
    ])
    check(locate_method_line(source, "testTrailingSpecial") == 4, "应定位到方法声明行")
    check(locate_method_line(source, "absent") is None, "找不到应返回 None")


def test_method_body():
    source = "\n".join([
        "public class FooTest {",
        "    public void setUp() {",
        "        assertEquals(1, 1);",
        "    }",
        "    public void testBar() {",
        '        String s = "literal { not a brace";',
        "        // comment } also not a brace",
        "        assertEquals(3, compute());",
        "    }",
        "    public void testAfter() {",
        "        assertTrue(true);",
        "    }",
        "}",
    ])
    # 从第 5 行（testBar 声明）抽，花括号配平应停在第 9 行的 '}'，不含 testAfter
    body, lines, truncated = extract_method_body(source, 5)
    check("testBar()" in body and "assertEquals(3, compute())" in body, body)
    check("testAfter" not in body and "setUp" not in body, "不应跨到相邻方法")
    check(lines == 5, f"应抽 5 行（声明..收尾），得 {lines}")
    check(not truncated, "完整方法不该标截断")

    # 字符串里的 "{" 与注释里的 "}" 不应影响配平
    check("literal {" in body and "comment }" in body, "字面量/注释原样保留")

    clipped, _lines, clipped_flag = extract_method_body(source, 5, max_chars=40)
    check(clipped_flag and "方法体截断" in clipped, clipped)

    check(extract_method_body(source, None)[1] == 0, "无起始行应返回空")
    check(extract_method_body(source, 999)[1] == 0, "越界应返回空")


def test_compile_errors():
    log = "\n".join([
        "Buildfile: build.xml",
        "    [javac] /home/u/ws/src/main/java/Foo.java:42: error: cannot find symbol",
        "    [javac]   symbol:   method missing()",
        "    [javac] /home/u/ws/src/main/java/Foo.java:42: error: cannot find symbol",
        "    [javac] /home/u/ws/src/main/java/Bar.java:7: incompatible types: int cannot be converted",
        "BUILD FAILED",
    ])
    text, count = extract_compile_errors(log)
    check(count == 2, f"重复行应去重，得 {count}: {text}")
    check("Foo.java:42:" in text and "Bar.java:7:" in text, text)
    check("/home/u/ws" not in text, "路径应压成 basename")
    check("cannot find symbol" in text, text)

    empty, n = extract_compile_errors("no compiler output here")
    check(empty == "" and n == 0, "提取不到应返回空，交由调用方回退原文")

    few, n1 = extract_compile_errors(log, max_errors=1)
    check(n1 == 1 and "Bar.java" not in few, few)


if __name__ == "__main__":
    test_command()
    test_scoring()
    test_format()
    test_failing_tests()
    test_method_body()
    test_compile_errors()
    print("search selftest: OK")
