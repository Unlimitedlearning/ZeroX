"""search_code 的纯逻辑：grep 命令构造、命中排序、结果整理。

默认字面匹配（-F）而非正则：Java 声明里的 [] () . 曾被 grep 当成正则，
导致 exit 2 且 stderr 被吞后误报「未找到关键字」。需要正则时显式传 regex=True。
只处理调用方传进来的文本，不访问文件系统，可离线单测。
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
