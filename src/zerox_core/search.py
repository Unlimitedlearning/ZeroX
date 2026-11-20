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


# ---------------- Java 结构索引 ----------------
# get_file_internal_structure 用：扫描类/方法/构造函数的签名与行范围。
# 旧正则强制访问修饰符，漏掉 Defects4J 里大量包级私有方法与全部构造函数；
# 这里改成修饰符可选 + 类型锚定，并复用花括号配平算结束行。
_ANNO = r"(?:@\w+(?:\([^)]*\))?\s*)*"
_MODS = (r"(?:public|protected|private|static|final|abstract|synchronized|native|"
         r"default|strictfp|sealed|non-sealed|transient|volatile)\s+")
_PRIM = r"(?:void|boolean|byte|char|short|int|long|float|double)"
_TYPE_NAME = r"[A-Z][\w$]*(?:\.[A-Za-z_$][\w$]*)*"
_TYPE_TOKEN = (r"(?:" + _PRIM + r"|" + _TYPE_NAME + r"\s*(?:<[^{};=]*?>)?\s*(?:\[\])*"
               r"|[A-Za-z_$][\w$]*\s*(?:<[^{};=]*?>)?\s*(?:\[\])+)")
_STRUCT_TYPE = re.compile(
    r"^\s*" + _ANNO + r"(?:" + _MODS + r")*(class|interface|enum|record)\s+([A-Za-z_$][\w$]*)"
)
_STRUCT_METHOD = re.compile(
    r"^\s*" + _ANNO + r"(?:" + _MODS + r")*(?:<[^{};]*?>\s*)?("
    + _TYPE_TOKEN + r")\s+([A-Za-z_$][\w$]*)\s*\("
)
_STRUCT_CTOR = re.compile(r"^\s*" + _ANNO + r"(?:" + _MODS + r")*([A-Z][\w$]*)\s*\(")
_NON_DECL = frozenset({"if", "for", "while", "switch", "catch", "try", "else", "do",
                       "return", "new", "throw", "assert", "case", "finally", "synchronized"})


def _split_params(raw):
    """按泛型/数组深度切参数，取每段的类型（去掉参数名）。"""
    segs, buf, depth = [], [], 0
    for ch in raw:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            segs.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        segs.append("".join(buf))
    out = []
    for seg in segs:
        s = re.sub(r"@\w+(?:\([^)]*\))?", "", seg).strip()
        s = re.sub(r"^final\s+", "", s).strip()
        parts = re.split(r"\s+", s)
        typ = " ".join(parts[:-1]) if len(parts) > 1 else s
        if typ:
            out.append(typ.replace(" ", ""))
    return ", ".join(out)


def _decl_params(lines, start_idx, max_lines=10):
    """从声明行起取括号内的参数类型；签名可能跨行，故拼接若干行再匹配。"""
    text = " ".join(lines[start_idx:start_idx + max_lines])
    m = re.search(r"\(([^()]*)\)", text)
    return _split_params(m.group(1)) if m else ""


def _decl_end_line(lines, start_idx):
    """声明的结束行（0-based）：分号结尾取当前行，否则花括号配平。"""
    depth, started, paren, in_block = 0, False, 0, False
    for pos in range(start_idx, len(lines)):
        masked, in_block = _mask_java_line(lines[pos], in_block)
        for ch in masked:
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren = max(0, paren - 1)
            elif ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
            elif ch == ";" and not started and paren == 0:
                return pos
        if started and depth <= 0:
            return pos
    return len(lines) - 1


def extract_java_structure(source, max_items=200):
    """扫描 Java 源码结构，返回 (items, truncated)。

    items 是按起始行升序的字典列表：
      {"line_start": 1-based, "line_end": 1-based, "kind": ..., "signature": ...}
    kind ∈ class/interface/enum/record/method/constructor；method/constructor 的
    signature 带参数类型，用于区分重载。纯函数，不读文件。
    """
    lines = str(source or "").splitlines()
    # 构造函数名必须是文件里声明过的类型，先收集一遍类型名，避免顺序依赖。
    type_names = set()
    in_block = False
    for raw in lines:
        masked, in_block = _mask_java_line(raw, in_block)
        m = _STRUCT_TYPE.match(masked)
        if m:
            type_names.add(m.group(2))

    raw_items, in_block = [], False
    for idx, raw in enumerate(lines):
        masked, in_block = _mask_java_line(raw, in_block)
        m = _STRUCT_TYPE.match(masked)
        if m:
            raw_items.append((idx, m.group(1), m.group(2)))
            continue
        m = _STRUCT_METHOD.match(masked)
        if m and m.group(2) not in _NON_DECL:
            raw_items.append((idx, "method", m.group(2)))
            continue
        m = _STRUCT_CTOR.match(masked)
        if m and m.group(1) in type_names:
            raw_items.append((idx, "constructor", m.group(1)))

    items = []
    for idx, kind, name in raw_items:
        end_idx = _decl_end_line(lines, idx)
        sig = f"{name}({_decl_params(lines, idx)})" if kind in ("method", "constructor") else name
        items.append({"line_start": idx + 1, "line_end": end_idx + 1,
                      "kind": kind, "signature": sig})
    return items[:max_items], len(items) > max_items


# ---------------- 行号锚点补丁 ----------------
# 模型报行号 + 一段锚点原文，服务端校验锚点后替换，无需抄写含缩进的原文。
# 所有 change 的锚点只对「本轮开始时的原始行数组」校验，避免前面的替换污染后续定位。
ANCHOR_MIN_CHARS = 8          # 锚点去空白后的最短长度，太短易歧义
ANCHOR_SEARCH_RADIUS = 3      # 行号漂移时在其 ±N 行内搜索唯一锚点

_LINE_ANCHOR_ERRORS = {
    "bad_line": "start_line 必须是正整数",
    "bad_range": "end_line 必须 >= start_line",
    "out_of_range": "行号越过文件末尾",
    "anchor_short": f"anchor 去空白后至少需要 {ANCHOR_MIN_CHARS} 个字符",
    "anchor_missing": "在指定行及附近未找到锚点，请重新 read_specific_code 核对",
    "anchor_ambiguous": "锚点匹配到多行，请换更独特的锚点或加长",
    "no_new": "缺少 new 内容；删除请显式传 op=delete",
}


def _anchor_text(s):
    """锚点比对用的归一化：去首尾空白、压缩内部空白，容忍缩进与空格差异。"""
    return re.sub(r"\s+", "", s or "")


def plan_line_patches(lines, changes):
    """把行号锚点的 changes 解析成待应用计划，纯计算、不碰文件。

    返回 (plans, error)：
        plans: [(idx0, end_idx0, new_lines, mode)]：
             replace/delete 的 idx 为 0-based 左闭右闭；insert 的 end_idx = start_idx-1，
             表示在 start 行之前插入；new_lines 为替换/插入进去的行列表。
      error: 出错时的人类可读描述，此时 plans 无意义。
    所有锚点都对传入的原始 lines 校验，不受其他 change 影响。
    校验顺序保证：任一 change 有问题就整轮拒绝（调用方据此不落盘）。
    """
    line_count = len(lines)
    plans = []
    for ch in changes:
        start = ch.get("start_line")
        end = ch.get("end_line", start)
        anchor = ch.get("anchor", "")
        op = ch.get("op", "replace")

        if not isinstance(start, int) or isinstance(start, bool) or start < 1:
            return None, _LINE_ANCHOR_ERRORS["bad_line"]
        if not isinstance(end, int) or isinstance(end, bool) or end < start:
            return None, _LINE_ANCHOR_ERRORS["bad_range"]
        if start > line_count or end > line_count:
            return None, _LINE_ANCHOR_ERRORS["out_of_range"]

        akey = _anchor_text(anchor)
        if len(akey) < ANCHOR_MIN_CHARS:
            return None, _LINE_ANCHOR_ERRORS["anchor_short"]

        # 锚点只在原始行数组上定位；优先指定行，漂移则在 ±R 行内找唯一匹配。
        start_idx = start - 1
        if akey not in _anchor_text(lines[start_idx]):
            candidates = [
                i for i in range(max(0, start_idx - ANCHOR_SEARCH_RADIUS),
                                 min(line_count, start_idx + ANCHOR_SEARCH_RADIUS + 1))
                if akey in _anchor_text(lines[i])
            ]
            if not candidates:
                return None, _LINE_ANCHOR_ERRORS["anchor_missing"]
            if len(candidates) > 1:
                return None, _LINE_ANCHOR_ERRORS["anchor_ambiguous"]
            start_idx = candidates[0]

        # end_line 随行号修正一起平移：模型看到的是连续区间，漂移是整段一起偏。
        shift = start_idx - (start - 1)
        end_idx = min(line_count - 1, end - 1 + shift)
        if end_idx < start_idx:
            end_idx = start_idx

        if op == "delete":
            new_lines = []
        elif op in ("replace", "insert"):
            if "new" not in ch or ch.get("new") is None:
                return None, _LINE_ANCHOR_ERRORS["no_new"]
            new_text = ch["new"]
            if not isinstance(new_text, str):
                return None, "new 必须是字符串"
            new_lines = new_text.replace("\r\n", "\n").split("\n")
        else:
            return None, "op 必须是 replace / delete / insert"

        plans.append((start_idx, end_idx, new_lines, op))

    # 倒序应用：先改大行号，前面的替换不影响未处理区间的下标。
    plans.sort(key=lambda p: p[0], reverse=True)
    return plans, None


def apply_line_patches(content, changes):
    """对整文件内容应用行号锚点补丁，返回 (new_content, error)。

    纯函数：不读写文件，调用方自行决定是否落盘。任一 change 校验失败则整轮拒绝，
    返回 (None, 错误文本)，保证「要么全成、要么全不动」，与 worklog 的原子性一致。
    """
    src = content.replace("\r\n", "\n")
    lines = src.split("\n")
    plans, err = plan_line_patches(lines, changes)
    if err:
        return None, err

    out = list(lines)
    for start_idx, end_idx, new_lines, mode in plans:
        if mode == "insert":
            # 在 start_idx 对应行之前插入（start_idx=0 即插到文件头）
            out[start_idx:start_idx] = new_lines
        else:
            out[start_idx:end_idx + 1] = new_lines
    return "\n".join(out), None
