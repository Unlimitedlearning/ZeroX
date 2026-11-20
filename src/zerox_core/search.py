"""search_code / run_test 的纯解析逻辑：grep 构造、结果整理、失败定位与编译错误提取。

不接触文件系统、目标环境或全局状态，便于离线单测。
server 只负责注入当前工作区与配置，解析/排序规则都在这里。
"""
import re
import shlex

DEFAULT_MAX_RESULTS = 20
# grep 扫到这个条数就停：既避免搜通用词时刷屏，也给「定义优先」留出足够的候选。
SCAN_LIMIT = 300

_TYPE_DECL = re.compile(
    r"^(?:(?:public|protected|private|static|final|abstract|strictfp)\s+)*"
    r"(?:class|interface|enum|record|@interface)\s+\w+"
)
_METHOD_DECL = re.compile(
    r"^(?:(?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)?"
    r"[\w<>\[\], ?]+\s+\w+\s*\([^;{]*\)\s*(?:throws\s+[\w, ]+)?[{;]"
)
_FIELD_DECL = re.compile(
    r"^(?:public|protected|private|static|final)\s+[\w<>\[\], ?]+\s+\w+\s*(?:=|;)"
)
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


def build_grep_command(scope, include, keyword, regex=False):
    """返回 (cmd, err)；err 非空表示参数非法，调用方应原样回给 agent。

    默认 -F 字面匹配：Java 声明里的 [] () . 等不是正则，曾导致 grep 把
    'public int[] add' 判为未闭合字符类（exit 2），而 stderr 被吞掉后
    被误报成「未找到关键字」。需要正则时显式传 regex=True 走 -E。
    '--' 阻止以 '-' 开头的 keyword 被当成 grep 选项。
    """
    kw = str(keyword or "")
    if not kw.strip():
        return None, "【参数错误】keyword 不能为空。"
    flag = "-E" if regex else "-F"
    return (
        f"grep -rn {flag} --include={shlex.quote(str(include))} "
        f"-- {shlex.quote(kw)} {shlex.quote(str(scope))} | head -n {SCAN_LIMIT}"
    ), None


def definition_score(text):
    """给命中行打分：类型定义 > 方法/构造 > 字段 > 普通引用。"""
    s = str(text).strip()
    if _TYPE_DECL.match(s):
        return 3
    if _METHOD_DECL.match(s):
        return 2
    if _FIELD_DECL.match(s):
        return 1
    return 0


def _relative(path, workdir):
    """把绝对路径压成工作区相对路径，read_specific_code 可直接接收。"""
    base = str(workdir or "").rstrip("/")
    if base and (str(path) == base or str(path).startswith(base + "/")):
        rel = str(path)[len(base):].lstrip("/")
        return rel or "."
    return str(path)


def format_results(stdout, keyword, workdir, include, max_results=DEFAULT_MAX_RESULTS,
                   regex=False):
    """把 grep 输出整理成带摘要的结果文本；扫描到的总数与显示条数都写明。

    grep 行格式为 <绝对路径>:<行号>:<内容>，内容本身可能含 ':'，故只切前两个。
    """
    try:
        limit = max(1, min(100, int(max_results)))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_RESULTS

    entries = []
    for line in str(stdout or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[1].strip().isdigit():
            continue
        path, lineno, text = parts
        rel = _relative(path, workdir)
        entries.append((definition_score(text), rel, int(lineno), text))

    entries.sort(key=lambda e: (-e[0], e[1], e[2]))
    shown = entries[:limit]
    body = "\n".join(f"{rel}:{no}:{text}" for _, rel, no, text in shown)
    mode = "正则匹配" if regex else "字面匹配"
    header = (
        f"搜索 '{keyword}'（{include}，{mode}）：扫描到 {len(entries)} 条命中，"
        f"按定义优先显示 {len(shown)} 条"
    )
    if len(entries) > len(shown):
        header += f"；缩小 file_pattern 或换更具体的关键字可查看其余 {len(entries) - len(shown)} 条"
    return header + "\n" + body


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


# 方法体抽取：从声明行起，靠花括号配平找到方法末尾；忽略字符串/字符/注释里的括号。
_LINE_COMMENT = "//"
_BLOCK_START = "/*"
_BLOCK_END = "*/"


def _mask_java_line(line, in_block):
    """把一行 Java 里的字符串/字符字面量与注释替换成空格，返回 (掩码行, 是否仍在块注释)。

    只用于花括号配平：被掩码的位置不会贡献 `{` / `}`，但保留原长度与其余字符。
    覆盖行注释、块注释（可跨行）、双引号/单引号及其转义。
    """
    out = []
    i, n = 0, len(line)
    while i < n:
        if in_block:
            end = line.find(_BLOCK_END, i)
            if end == -1:
                out.append(" " * (n - i))
                return "".join(out), True
            out.append(" " * (end + 2 - i))
            i = end + 2
            in_block = False
            continue
        if line.startswith(_LINE_COMMENT, i):
            out.append(" " * (n - i))
            break
        if line.startswith(_BLOCK_START, i):
            in_block = True
            out.append(" " * 2)
            i += 2
            continue
        if line[i] in ('"', "'"):
            quote = line[i]
            out.append(" ")
            i += 1
            while i < n:
                out.append(" ")
                if line[i] == "\\":
                    i += 1
                    if i < n:
                        out.append(" ")
                        i += 1
                    continue
                if line[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        out.append(line[i])
        i += 1
    return "".join(out), in_block


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
        masked, in_block = _mask_java_line(raw, in_block)
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
