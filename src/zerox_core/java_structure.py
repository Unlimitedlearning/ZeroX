"""get_file_internal_structure 的底层纯逻辑：Java 类/方法/构造函数结构索引。

只在**语言规则**这一层绑定 Java：顶部的修饰符表、原始类型表、record/sealed 关键字、
「构造函数名 == 类名」的判定都写在 _MODS / _PRIM / _STRUCT_* 里。
算法骨架是语言中立的——两遍扫描收集类型名、按声明锚点提取、花括号配平算起止行。
将来要支持别的语言，替换顶部正则与 kind 映射即可，下面的扫描逻辑可原样继承。

旧正则强制访问修饰符，漏掉包级私有方法与全部构造函数；这里改成修饰符可选 +
类型锚定。掩码逻辑依赖最底层的 zerox_core.source_lex。只解析传进来的源码文本，可离线单测。
"""

import re

from zerox_core.source_lex import mask_source_line


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
        masked, in_block = mask_source_line(lines[pos], in_block)
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
        masked, in_block = mask_source_line(raw, in_block)
        m = _STRUCT_TYPE.match(masked)
        if m:
            type_names.add(m.group(2))

    raw_items, in_block = [], False
    for idx, raw in enumerate(lines):
        masked, in_block = mask_source_line(raw, in_block)
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
