"""ZeroX 内部模块（src/zerox_core/）。

这里只放「零运行期状态、可被多处复用」的东西：

    config.py    配置默认值 / 合并 / 校验 / 正则编译 / resolve_bug（纯函数）
    schemas.py   MCP 工具 schema（纯数据，改描述文案只改这里）
    exec_env.py  命令执行器：把 WSL 转发 / 原生 bash 的差异收在一个地方
    PASSED_RESULT_MARKER  服务端通过文案 / 客户端正样本判定共用的协议常量

依赖方向是单向的：入口（src/zerox.py）、src/zerox_server.py、src/audit.py 都依赖本包，
本包不依赖它们任何一个，也不持有任何全局状态 —— 运行期状态在 server 里，
事件流和历史在 worklog.py 里，判定在 audit.py 里。

命名说明：本包叫 zerox_core 而不是 zerox，是因为入口脚本已经占用了 src/zerox.py
（同名的模块会盖住同名目录，两者不能共存）。
"""

# 服务端 run_test 在 passed_pattern 命中时回这句话；客户端只在见到它之后才承认 [FIXED]。
# 两边必须共用同一个常量：以前各写一份字面量，改漏一处就会把所有结果记成 Failed。
PASSED_RESULT_MARKER = "【测试全部通过】"
