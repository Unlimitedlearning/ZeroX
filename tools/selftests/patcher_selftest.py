"""patch_and_log 底层（zerox_core.patcher + source_lex）纯逻辑自检：不连 WSL。

    python tools/selftests/patcher_selftest.py

覆盖：
  1. 行号锚点补丁：基本替换/缩进容忍/漂移修正/多处倒序/删除/插入
  2. 锚点拒绝：过短/缺失/歧义/越界/坏行号/缺 new
  3. brace_balance：注释/字符串/跨行块注释里的括号不误计
  4. 落盘前配平自检：原文配平而补丁后失衡则拒绝（花/圆/方括号），
     修正补丁（失衡->配平）放行，原文本就失衡时不强制
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.patcher import apply_line_patches  # noqa: E402
from zerox_core.patcher import brace_balance  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)


SAMPLE = "\n".join([
    "line0 content here",
    "    private static final byte SPACE = 32;",
    "line2",
    "line3",
    "    public void testFoo() {",
    "line5",
    "line6",
])


def test_line_anchor_apply():
    # 1. 基本替换：只改锚点行，其余不变
    out, err = apply_line_patches(SAMPLE, [{
        "start_line": 2, "anchor": "private static final byte SPACE",
        "new": "    private static final byte SPACE = 65; // changed"}])
    check(err is None, f"基本替换不应报错: {err}")
    check("SPACE = 65" in out and "line2" in out, out)

    # 2. 缩进容忍：锚点不带缩进也应匹配
    out, err = apply_line_patches(SAMPLE, [{
        "start_line": 2, "anchor": "private static final byte SPACE", "new": "X"}])
    check(err is None and out.splitlines()[1] == "X", f"缩进容忍失败: {out}, {err}")

    # 3. 行号漂移：写成 3，真实锚点在 2，±3 内应自动修正
    out, err = apply_line_patches(SAMPLE, [{
        "start_line": 3, "anchor": "private static final byte SPACE", "new": "FIXED"}])
    check(err is None and "FIXED" in out, f"漂移修正失败: {out}, {err}")

    # 4. 多处 change 倒序应用：两个不同行都被正确替换
    out, err = apply_line_patches(SAMPLE, [
        {"start_line": 2, "anchor": "private static final byte SPACE", "new": "AAA"},
        {"start_line": 5, "anchor": "public void testFoo()", "new": "BBB"}])
    check(err is None and "AAA" in out and "BBB" in out, f"倒序应用失败: {out}, {err}")

    # 5. 多行范围替换
    out, err = apply_line_patches(SAMPLE, [{
        "start_line": 2, "end_line": 3, "anchor": "private static final byte SPACE", "new": "ONE"}])
    check(err is None and "line2" not in out and "ONE" in out, f"多行替换失败: {out}, {err}")

    # 6. delete 删行
    out, err = apply_line_patches(SAMPLE, [{
        "start_line": 2, "end_line": 3, "anchor": "private static final byte SPACE", "op": "delete"}])
    check(err is None and "SPACE" not in out and "line2" not in out, f"删除失败: {out}, {err}")

    # 7. insert 插到文件头
    out, err = apply_line_patches(SAMPLE, [{
        "start_line": 1, "anchor": "line0 content here", "op": "insert", "new": "HEADER"}])
    check(err is None and out.splitlines()[0] == "HEADER", f"头部插入失败: {out}, {err}")


def test_line_anchor_rejects():
    # 锚点过短
    out, err = apply_line_patches(SAMPLE, [{"start_line": 2, "anchor": "SP", "new": "X"}])
    check(out is None and "至少" in err, f"过短应拒绝: {err}")

    # 锚点缺失
    out, err = apply_line_patches(SAMPLE, [{"start_line": 2, "anchor": "noSuchMethodAnywhere", "new": "X"}])
    check(out is None and "未找到" in err, f"缺失应拒绝: {err}")

    # 锚点歧义：指定行不含锚点、需在 ±3 内搜索时找到多处匹配
    dup = "\n".join(["dup line same here", "x", "dup line same here"])
    out, err = apply_line_patches(dup, [{"start_line": 2, "anchor": "dup line same here", "new": "Z"}])
    check(out is None and "多行" in err, f"歧义应拒绝: {err}")

    # 越界
    out, err = apply_line_patches(SAMPLE, [{"start_line": 999, "anchor": "private static final byte SPACE", "new": "X"}])
    check(out is None and "越过" in err, f"越界应拒绝: {err}")

    # 非法行号
    out, err = apply_line_patches(SAMPLE, [{"start_line": 0, "anchor": "private static final byte SPACE", "new": "X"}])
    check(out is None and "正整数" in err, f"非法行号应拒绝: {err}")

    # 缺 new
    out, err = apply_line_patches(SAMPLE, [{"start_line": 2, "anchor": "private static final byte SPACE"}])
    check(out is None and "缺少" in err, f"缺 new 应拒绝: {err}")


BALANCED_JAVA = "\n".join([
    "class Sample {",
    "    void foo() {",
    "        int[] xs = new int[2];",
    "    }",
    "}",
])


def test_brace_balance():
    check(brace_balance(BALANCED_JAVA) == {"{": 0, "(": 0, "[": 0},
          f"配平源码应全为 0: {brace_balance(BALANCED_JAVA)}")
    # 注释、字符串、字符字面量里的括号不算
    tricky = 'class X { /* } */ String s = "{([)}"; char c = \'}\'; // {\n}'
    check(brace_balance(tricky) == {"{": 0, "(": 0, "[": 0},
          f"注释/字符串括号不应计入: {brace_balance(tricky)}")
    # 跨行块注释
    multi = "class X {\n /* \n } ( \n */ \n}"
    check(brace_balance(multi) == {"{": 0, "(": 0, "[": 0},
          f"跨行块注释括号不应计入: {brace_balance(multi)}")


def test_brace_guard():
    # 1. 原文配平、补丁少一个右括号 -> 拒绝
    out, err = apply_line_patches(BALANCED_JAVA, [{
        "start_line": 3, "anchor": "int[] xs = new int", "op": "delete"}])
    # 删的是 int[] xs 行（不含花括号），余额仍 0，应放行
    check(err is None, f"删非括号行不应被拒: {err}")

    # 少一个 }：把「字段行 + 方法结束行」两行换成一行，丢掉右括号
    out, err = apply_line_patches(BALANCED_JAVA, [{
        "start_line": 3, "end_line": 4, "anchor": "int[] xs = new int",
        "new": "        int x = 1;"}])
    check(out is None and "括号配平" in err and "花括号" in err,
          f"破坏花括号配平应拒绝: out={out}, err={err}")

    # 多一个 { 
    out, err = apply_line_patches(BALANCED_JAVA, [{
        "start_line": 2, "anchor": "    void foo()", "new": "    void foo() {{"}])
    check(out is None and "括号配平" in err, f"多写花括号应拒绝: {err}")

    # 圆括号失衡
    out, err = apply_line_patches(BALANCED_JAVA, [{
        "start_line": 2, "anchor": "    void foo()", "new": "    void foo("}])
    check(out is None and "圆括号" in err, f"圆括号失衡应拒绝: {err}")

    # 方括号失衡
    out, err = apply_line_patches(BALANCED_JAVA, [{
        "start_line": 3, "anchor": "        int[] xs = new int[2];",
        "new": "        int[] xs = new int[2;"}])
    check(out is None and "方括号" in err, f"方括号失衡应拒绝: {err}")

    # 2. 修正补丁：原文失衡、补丁后配平 -> 必须放行
    broken = "class X {\n    void foo() {\n        int value = 1;\n}"
    out, err = apply_line_patches(broken, [{
        "start_line": 4, "end_line": 4, "anchor": "        int value = 1;",
        "new": "        int value = 1;\n    }"}])
    check(err is None and brace_balance(out) == {"{": 0, "(": 0, "[": 0},
          f"修正补丁应放行: out={out}, err={err}")

    # 3. 原文就失衡、补丁后仍失衡 -> 不强制拦截
    out, err = apply_line_patches(broken, [{
        "start_line": 2, "anchor": "    void foo()", "new": "    void bar()"}])

    check(err is None, f"原文失衡的补丁不应被强制拒绝: {err}")

    # 4. 注释里的括号漂移不误报：替换行带注释独立的 }
    src = 'class X {\n    void foo() { // } \n    }\n}'
    out, err = apply_line_patches(src, [{
        "start_line": 2, "anchor": "    void foo()",
        "new": '    int foo() { // } '}])
    check(err is None, f"注释里的右括号不应误报: {err}")


if __name__ == "__main__":
    test_line_anchor_apply()
    test_line_anchor_rejects()
    test_brace_balance()
    test_brace_guard()
    print("patcher selftest: OK")
