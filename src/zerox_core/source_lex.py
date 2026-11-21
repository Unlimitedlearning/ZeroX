"""源码词法掩码：把字符串/字符/注释里的字符替换成空格，供括号配平使用。

这里处理的记号（//、/* */、"..."、'...'、反斜杠转义）是 C 系语言共享的子集：
Java / C / C++ / C# / JS / TS / Go / Rust / Kotlin 都适用，不绑定 Java。
刻意不认 Java text block（\"\"\"）等单语言特性，避免为一门语言增加别语言的误判。

被 test_output_parser.py（方法体抽取）、java_structure.py（结构索引）与
patcher.py（补丁落盘前配平自检）共同依赖，放在最底层避免三者互相 import。
纯函数、无状态。掩码保留原长度与其余字符，只把被遮蔽位置换成空格，
所以调用方按列号/括号计数都仍准确。
"""
_LINE_COMMENT = "//"
_BLOCK_START = "/*"
_BLOCK_END = "*/"


def mask_source_line(line, in_block):
    """把一行 Java 里的字符串/字符字面量与注释替换成空格，返回 (掩码行, 是否仍在块注释)。

    覆盖行注释、块注释（可跨行）、双引号/单引号及其转义。被掩码的位置不会贡献
    `{` / `}` 等括号，但保留原长度与其余字符。
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
