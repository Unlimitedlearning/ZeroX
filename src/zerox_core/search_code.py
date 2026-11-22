"""search_code 的纯逻辑：grep 命令构造、命中排序、结果整理、声明行范围标注。

默认字面匹配（-F）而非正则：Java 声明里的 [] () . 曾被 grep 当成正则，
导致 exit 2 且 stderr 被吞后误报「未找到关键字」。需要正则时显式传 regex=True。
只处理调用方传进来的文本，不访问文件系统，可离线单测；需要源码时由调用方注入
loader（见 format_results 的 source_loader），本模块不自行读文件。
"""

import re
import shlex

from zerox_core.java_structure import extract_java_structure


DEFAULT_MAX_RESULTS = 20
# grep 扫到这个条数就停：既避免搜通用词时刷屏，也给「定义优先」留出足够的候选。
SCAN_LIMIT = 300
# 一次搜索最多回读几个文件算声明范围：命中散落在很多文件时，回读成本按文件数线性增长。
MAX_RANGE_FILES = 5
# 单个文件只取前 N 行：声明范围只依赖结构，超长文件不必整份穿过管道。
RANGE_SCAN_LINES = 4000
# 只有这两类命中的行号本身就是一个声明，范围即声明自身，不必推断包含关系。
_RANGE_KINDS = ("method", "constructor")
# 定义分达到这个值才按声明处理（见 definition_score：3=类型、2=方法/构造、1=字段、0=引用）
_RANGE_MIN_SCORE = 2

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


def parse_hits(stdout, workdir):
    """grep 输出 -> [(定义分, 相对路径, 行号, 原文)]，畸形行跳过。

    grep 行格式为 <绝对路径>:<行号>:<内容>，内容本身可能含 ':'，故只切前两个。
    """
    hits = []
    for line in str(stdout or "").splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[1].strip().isdigit():
            continue
        path, lineno, text = parts
        hits.append((definition_score(text), _relative(path, workdir), int(lineno), text))
    return hits


def _declared_ranges(shown, source_loader):
    """给「声明行」类命中算出行范围，返回 {显示序号: (起, 止)}。

    只标注定义分 >= 2 的命中：这条命中的行号本身就是声明，范围即该声明自身，
    不需要推断「这一行被谁包含」，也不会给出与命中无关的区间。字段（1 分）多为单行，
    普通引用（0 分）的范围取决于所在成员，两者都不标注。

    源码由调用方注入：source_loader(rels) 一次取回多个文件，返回 {相对路径: 源码}。
    回读失败、文件超出 RANGE_SCAN_LINES、结构解析不出该声明行时，该条不标注 ——
    标注是增益，不是结果正确性的前提。
    """
    if source_loader is None:
        return {}
    need = []
    for score, rel, _, _ in shown:
        if score >= _RANGE_MIN_SCORE and rel not in need:
            need.append(rel)
    if not need:
        return {}
    try:
        sources = source_loader(need[:MAX_RANGE_FILES]) or {}
    except Exception:  # 回读是增强路径，任何失败都只表现为「没有范围」
        return {}

    spans = {}
    for rel in need:
        src = sources.get(rel)
        if not src:
            continue
        items, _ = extract_java_structure(src)
        spans[rel] = {it["line_start"]: (it["line_start"], it["line_end"])
                      for it in items if it["kind"] in _RANGE_KINDS}
    return {idx: spans[rel][no] for idx, (score, rel, no, _) in enumerate(shown)
            if no in spans.get(rel, {})}


def format_results(stdout, keyword, workdir, include, max_results=DEFAULT_MAX_RESULTS,
                   regex=False, source_loader=None):
    """把 grep 输出整理成带摘要的结果文本；扫描到的总数与显示条数都写明。

    命中是方法/构造函数声明时，行尾附上该声明的 [起-止] 行号，可直接交给
    read_specific_code：实测读取区间中位 71 行而所在方法中位 20 行，模型原本要靠
    另一次结构查询才能知道边界。标注只增加十余字符，换来的是少读方法外的代码。
    """
    try:
        limit = max(1, min(100, int(max_results)))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_RESULTS

    entries = parse_hits(stdout, workdir)
    entries.sort(key=lambda e: (-e[0], e[1], e[2]))
    shown = entries[:limit]
    ranges = _declared_ranges(shown, source_loader)

    rows = []
    for idx, (_, rel, no, text) in enumerate(shown):
        span = ranges.get(idx)
        rows.append(f"{rel}:{no}:{text}  [{span[0]}-{span[1]}]" if span
                    else f"{rel}:{no}:{text}")
    body = "\n".join(rows)

    mode = "正则匹配" if regex else "字面匹配"
    header = (
        f"搜索 '{keyword}'（{include}，{mode}）：扫描到 {len(entries)} 条命中，"
        f"按定义优先显示 {len(shown)} 条"
    )
    if ranges:
        header += "；行尾 [起-止] 为该声明的行范围，可直接作为读取区间"
    if len(entries) > len(shown):
        header += f"；缩小 file_pattern 或换更具体的关键字可查看其余 {len(entries) - len(shown)} 条"
    return header + "\n" + body
