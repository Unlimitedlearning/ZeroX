"""Java 结构索引纯逻辑自检：包级私有/构造函数/重载/跨行签名/注释掩码；不连 WSL。

    python tools/selftests/java_structure_selftest.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.java_structure import extract_java_structure  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)


SAMPLE_JAVA = "\n".join([
    "class Sample {",
    "    // public void commentedOut() { }",
    "    /* private void alsoCommented() { } */",
    "    Sample(int[] data) {",
    "        this.data = data;",
    "    }",
    "    int size() { return data.length; }",
    "    void encode(String s) { }",
    "    void encode(String s, String enc) {",
    "            // ignored",
    "    }",
    "    public static void main(String[] args) {",
    "    }",
    '    String withBrace() { return "{ not a brace"; }',
    "}",
])


def test_java_structure():
    items, truncated = extract_java_structure(SAMPLE_JAVA)
    check(not truncated, "小文件不应截断")
    sigs = [(i["kind"], i["signature"]) for i in items]
    check(("class", "Sample") in sigs, f"缺类声明: {sigs}")
    check(("constructor", "Sample(int[])") in sigs, f"缺构造函数: {sigs}")
    check(("method", "size()") in sigs, f"缺包级私有方法: {sigs}")
    check(("method", "main(String[])") in sigs, f"缺 public 方法: {sigs}")
    check(("method", "encode(String)") in sigs, f"缺重载1: {sigs}")
    check(("method", "encode(String, String)") in sigs, f"缺重载2: {sigs}")
    check(all("commentedOut" not in s and "alsoCommented" not in s for _, s in sigs),
          f"注释里的假声明不应出现: {sigs}")

    bysig = {i["signature"]: i for i in items}
    check(bysig["encode(String, String)"]["line_start"] == 9
          and bysig["encode(String, String)"]["line_end"] == 11,
          f"跨行签名起止行错误: {bysig['encode(String, String)']}")
    check(bysig["size()"]["line_start"] == 7 and bysig["size()"]["line_end"] == 7,
          f"单行方法起止行错误: {bysig['size()']}")
    check(bysig["Sample"]["line_end"] == 15, f"类结束行错误: {bysig['Sample']}")
    check(bysig["withBrace()"]["line_end"] == 14,
          f"字符串里的括号不应干扰配平: {bysig['withBrace()']}")

    # 接口里分号结尾的抽象方法：起止行即声明行
    iface = "interface I {\n    void run();\n    int size();\n}"
    its, _ = extract_java_structure(iface)
    run = [i for i in its if i["signature"] == "run()" and i["kind"] == "method"]
    check(len(run) == 1 and run[0]["line_start"] == 2 and run[0]["line_end"] == 2,
          f"接口抽象方法起止行错误: {its}")

    # 截断标记
    its, tr = extract_java_structure(SAMPLE_JAVA, max_items=2)
    check(len(its) == 2 and tr, "超过上限应截断并标记")


if __name__ == "__main__":
    test_java_structure()
    print("java_structure selftest: OK")
