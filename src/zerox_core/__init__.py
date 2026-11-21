"""ZeroX 内部模块（src/zerox_core/）。

这里只放「零运行期状态、可被多处复用」的东西：

    config.py            配置默认值 / 合并 / 校验 / 正则编译 / resolve_bug（纯函数）
    schemas.py           MCP 工具 schema（纯数据，改描述文案只改这里）
    exec_env.py          命令执行器：把 WSL 转发 / 原生 bash 的差异收在一个地方
    context.py           上下文裁剪纯函数：对称截工具输出/补丁参数、按价值裁剪轮次、数体积
    search_code.py       search_code 的 grep 构造 / 命中排序 / 结果整理
    test_output_parser.py  run_test 失败诊断：失败定位/编译错误/断言原文/方法体/日志截断
    java_structure.py    get_file_internal_structure 的 Java 类/方法结构索引
    source_lex.py         C 系通用词法掩码（字符串/字符/注释），被诊断/结构/补丁三个模块共用
    patcher.py           行号锚点补丁 + 落盘前括号配平自检（patch_and_log 的底层纯函数）
    PASSED_RESULT_MARKER  服务端通过文案 / 客户端正样本判定共用的协议常量
    TASK_FAILED_MARKER    服务端测试次数用尽时的强制终止文案 / 客户端退出判定共用

依赖方向是单向的：入口（src/zerox.py）、src/zerox_server.py、src/audit.py 都依赖本包，
本包不依赖它们任何一个，也不持有任何全局状态 —— 运行期状态在 server 里，
事件流和历史在 worklog.py 里，判定在 audit.py 里。

命名说明：本包叫 zerox_core 而不是 zerox，是因为入口脚本已经占用了 src/zerox.py
（同名的模块会盖住同名目录，两者不能共存）。
"""

# 服务端 run_test 在 passed_pattern 命中时回这句话；客户端只在见到它之后才承认 [FIXED]。
# 两边必须共用同一个常量：以前各写一份字面量，改漏一处就会把所有结果记成 Failed。
PASSED_RESULT_MARKER = "【测试全部通过】"

# 服务端 max_tests 用尽时回这句话并要求 agent 输出 [FAILED]；客户端见到它就强制退出循环。
# 与 PASSED_RESULT_MARKER 同理：两端必须共用，改漏一处会让超限任务卡死在主循环里。
TASK_FAILED_MARKER = "【任务失败】"
