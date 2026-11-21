"""行号锚点补丁：模型报行号 + 一段锚点原文，服务端校验锚点后替换，无需抄写缩进。

    所有 change 的锚点只对「本轮开始时的原始行数组」校验，避免前面的替换污染后续定位。
    纯计算、不读写文件，调用方自行决定是否落盘，便于离线单测。

依赖 source_lex 的掩码做括号配平自检；不依赖 search，依赖方向保持单向。
"""
import re

from zerox_core.source_lex import mask_source_line

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
    "bad_new_type": "new 必须是字符串",
    "bad_op": "op 必须是 replace / delete / insert",
    "brace_unbalanced": "补丁会破坏括号配平",
}


def brace_balance(source):
    """统计源码里 { } ( ) [ ] 的净余额（左减右），忽略注释与字符串中的括号。

    返回 {"{": n, "(": n, "[": n}：n>0 表示缺右括号，n<0 表示缺左括号。
    只用于「原文配平、补丁后不配平」的坏补丁拦截；复用 mask_source_line 逐行掩码，
    故块注释跨行、行注释、双/单引号及其转义都不会误计。
    """
    counts = {"{": 0, "(": 0, "[": 0}
    pairs = {"}": "{", ")": "(", "]": "["}
    in_block = False
    for line in (source or "").replace("\r\n", "\n").split("\n"):
        masked, in_block = mask_source_line(line, in_block)
        for ch in masked:
            if ch in counts:
                counts[ch] += 1
            elif ch in pairs:
                counts[pairs[ch]] -= 1
    return counts


def unbalanced_detail(balance):
    """把 brace_balance 的结果转成人类可读的失衡描述；全配平返回空串。"""
    names = {"{": "花括号 {}", "(": "圆括号 ()", "[": "方括号 []"}
    parts = []
    for kind, n in balance.items():
        if n > 0:
            parts.append(f"{names[kind]}缺 {n} 个右括号")
        elif n < 0:
            parts.append(f"{names[kind]}多 {-n} 个右括号（或缺左括号）")
    return "；".join(parts)


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
                return None, _LINE_ANCHOR_ERRORS["bad_new_type"]
            new_lines = new_text.replace("\r\n", "\n").split("\n")
        else:
            return None, _LINE_ANCHOR_ERRORS["bad_op"]

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
    patched = "\n".join(out)

    # 落盘前括号配平自检：只对「原文三种括号全配平」的干净文件强制。
    # 原文本就不配平的文件（例如上一轮故意留的半截状态、或含掩码未覆盖的语法）
    # 跳过，允许修正补丁把余额改回来——否则会挡住合法的修复。
    # 这条规则来自 26-bug 实测：33 个补丁快照里，原文配平的 30 个中，
    # 仅这 3 个补丁后失衡的恰好全是编译失败补丁，零误报零漏报。
    before_bal = brace_balance(src)
    if all(v == 0 for v in before_bal.values()):
        after_bal = brace_balance(patched)
        if any(v != 0 for v in after_bal.values()):
            detail = unbalanced_detail(after_bal)
            return None, (
                f"{_LINE_ANCHOR_ERRORS['brace_unbalanced']}：{detail}。"
                "请检查 new 文本是否漏写或多写了括号、是否漏掉整行代码，"
                "修订后重新提交本轮补丁。"
            )

    return patched, None
