"""run_test 失败诊断的纯逻辑：失败用例定位、编译错误/断言原文提取、方法体抽取、日志截断。

只解析调用方传进来的文本（Defects4J 输出 / surefire 报告 / 源码片段），
不访问文件系统、不持有状态，可离线单测。掩码逻辑依赖最底层的 zerox_core.source_lex。

分两段：失败用例定位、日志/输出解析。
"""

import html
import re

from zerox_core.source_lex import mask_source_line


# ---------------- run_test 诊断 · 失败用例定位 ----------------
# Defects4J 的 Failing tests 段形如 "- org.apache.Foo::testBar"
_TEST_REF = re.compile(
    r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)::([A-Za-z_$][\w$]*)\b"
)
_METHOD_DECL_LINE = re.compile(
    r"^\s*(?:public|protected|private)\s+[\w<>\[\], ?]+\s+(\w+)\s*\("
)


def parse_failing_tests(detail):
    """从 Failing tests 段提取 (类全名, 方法名)，去重保序。

    只认 `Class::method` 这种 Defects4J 格式；返回空列表时调用方跳过定位，
    不猜测测试名，避免把无关的类塞给模型。
    """
    out, seen = [], set()
    for match in _TEST_REF.finditer(str(detail or "")):
        pair = (match.group(1), match.group(2))
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def locate_method_line(source, method):
    """返回方法定义所在行号（1-based）；仅认带访问修饰符的声明，找不到返回 None。"""
    for number, line in enumerate(str(source or "").splitlines(), 1):
        match = _METHOD_DECL_LINE.match(line)
        if match and match.group(1) == method:
            return number
    return None


# ---------------- run_test 诊断 · 日志/输出解析 ----------------
# 编译器报错行：javac / ant / maven 常见形态，抓 file:line 与后面的错误描述。
# 只认带 .java:<数字> 的行，避免把普通日志/路径当错误；文件名允许 /、\ 与 $。
_COMPILE_ERROR = re.compile(
    r"(?P<path>[^\s:]+\.java):(?P<line>\d+)"
    r"(?:,\d+)?:?\s*(?:error:\s*)?(?P<msg>.+?)\s*$"
)


def extract_compile_errors(log, max_errors=8, msg_chars=200):
    """把编译器输出里的 `文件:行号: 错误` 提取成去重清单，返回 (文本, 条数)。

    编译失败时日志动辄几百行，模型却只需要「哪个文件第几行报什么」。纯文本解析，
    提取不到就返回空文本 / 0，让调用方回退原文。路径压成 basename，避免刷屏。
    """
    found, seen = [], set()
    for raw in str(log or "").splitlines():
        match = _COMPILE_ERROR.search(raw)
        if not match:
            continue
        path = match.group("path").replace("\\", "/").rsplit("/", 1)[-1]
        line = match.group("line")
        msg = match.group("msg").strip()
        key = (path, line, msg[:40])
        if key in seen:
            continue
        seen.add(key)
        if len(msg) > msg_chars:
            msg = msg[:msg_chars] + "…"
        found.append(f"- {path}:{line}: {msg}")
        if len(found) >= max_errors:
            break
    if not found:
        return "", 0
    return ("【编译错误（本地提取）】\n" + "\n".join(found), len(found))


# 断言原文提取：JUnit ComparisonFailure / TestNG / Hamcrest / surefire XML 属性。
# XML 实体先解码（&lt; → <），这样 `<failure message="expected:&lt;x&gt; but was:&lt;y&gt;">`
# 与纯文本堆栈能用同一组模式；每条模式都要求成对的 expected/but was 结构，
# 单独出现的 expectedTime、expectedValue 之类变量名不会被误判。
_ASSERTION_PATTERNS = (
    re.compile(r"expected:\s*<\s*(.{1,240}?)\s*>\s*but\s+was:\s*<\s*(.{1,240}?)\s*>", re.I | re.S),
    re.compile(r"expected\s+\[\s*(.{1,240}?)\s*\]\s*but\s+found\s+\[\s*(.{1,240}?)\s*\]", re.I | re.S),
    re.compile(r"expected:\s*(?!<|\[)(.{1,240}?)\s+but\s+was:\s*(?!<|\[)(.{1,240}?)(?=\s*(?:\n|$|at\s|\s{2}))", re.I),
    re.compile(r"Expected:\s*(.{1,240}?)\s+but:\s*was\s+(.{1,240}?)(?=\s*(?:\n|$))", re.I | re.S),
)


def _clean_assertion_value(value, limit):
    """压平空白并截断断言值；保留原文字符，只把换行/制表换成空格。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def cap_log_text(head, tail, total_bytes):
    """把超长日志的头尾片段拼成一段带省略标记的文本。纯函数，不读文件。

    head/tail 是调用方按字节上限读取并 decode 好的片段；total_bytes 只用于提示
    原文多大。日志超过上限时中间往往是探针刷屏的重复输出，丢掉它不影响 BUILD
    结果与 Failing tests（都在尾部），却能避免数百 MB 文本上的正则卡死。
    """
    size_mb = (total_bytes or 0) / (1024 * 1024)
    marker = f"\n... [日志 {size_mb:.1f}MB 过大，中间已省略] ...\n"
    return f"{head or ''}{marker}{tail or ''}"


def extract_assertions(log, max_pairs=4, value_chars=180):
    """从测试日志或 surefire 报告文本提取 (expected, actual) 对，返回 (文本, 条数)。

    数据源是调用方给的原文：可以是一整段 test.log，也可以是某个 *.xml / *.txt 报告。
    提取不到返回 ("", 0)，调用方据此回退到「取法见系统提示」，不会误报断言值。
    去重保序，值里的换行压成空格并按 value_chars 截断，避免长期望值刷屏。
    """
    text = html.unescape(str(log or ""))
    pairs, seen = [], set()
    for pattern in _ASSERTION_PATTERNS:
        for match in pattern.finditer(text):
            expected = _clean_assertion_value(match.group(1), value_chars)
            actual = _clean_assertion_value(match.group(2), value_chars)
            if not expected and not actual:
                continue
            key = (expected, actual)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
            if len(pairs) >= max_pairs:
                break
        if len(pairs) >= max_pairs:
            break
    if not pairs:
        return "", 0
    lines = [f"- expected: {exp!r} / actual: {act!r}" for exp, act in pairs]
    return "【断言原文（本地提取）】\n" + "\n".join(lines), len(pairs)


def extract_method_body(source, start_line, max_lines=60, max_chars=1600):
    """从 start_line（1-based，方法声明行）抽取方法体，返回 (带行号文本, 提取行数, 截断标记)。

    server 已经定位到失败用例的方法声明行，这里顺手把方法体读出来附上，
    模型就不必再发 get_file_internal_structure + read_specific_code 两轮工具调用。
    花括号配平保证只取一个方法；掩码处理避免字符串/注释里的括号提前收尾。
    """
    if not start_line or start_line < 1:
        return "", 0, False
    lines = str(source or "").splitlines()
    idx = start_line - 1
    if idx >= len(lines):
        return "", 0, False

    picked, depth, in_block, started = [], 0, False, False
    for offset in range(max_lines):
        pos = idx + offset
        if pos >= len(lines):
            break
        raw = lines[pos]
        picked.append((pos + 1, raw))
        masked, in_block = mask_source_line(raw, in_block)
        for ch in masked:
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
        # 声明行可能不带 '{'（如泛型/换行签名）；遇到第一个 '{' 后才允许配平收尾
        if started and depth <= 0:
            break

    truncated = len(picked) >= max_lines and (started and depth > 0 or not started)
    numbered = "\n".join(f"{no}: {text}" for no, text in picked)
    body = numbered
    body_truncated = False
    if len(body) > max_chars:
        body = body[:max_chars] + f"\n…（方法体截断，原长 {len(numbered)} 字；完整内容用 read_specific_code 读）"
        body_truncated = True
    return body, len(picked), (truncated or body_truncated)
