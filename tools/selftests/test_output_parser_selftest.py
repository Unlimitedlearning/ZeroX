"""run_test 失败诊断纯逻辑自检：失败定位、方法体、编译错误；不连 WSL。

    python tools/selftests/test_output_parser_selftest.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.test_output_parser import (  # noqa: E402
    extract_compile_errors,
    extract_method_body,
    locate_method_line,
    parse_failing_tests,
)


def check(cond, message):
    if not cond:
        raise AssertionError(message)


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
    test_failing_tests()
    test_method_body()
    test_compile_errors()
    print("test_output_parser selftest: OK")
