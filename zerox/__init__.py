"""ZeroX 内部模块。

按「状态所有权」拆分，依赖方向严格单向：
    zerox_server.py -> tools/* -> (policy, guards, persist) -> (exec_env, fsops) -> (config, state)

约定：可变全局状态统一放 zerox/state.py，一律用 ``from zerox import state as S`` 加
``S.X = ...`` 读写；绝不 ``from zerox.state import X``（那是拷贝，改了不生效）。
"""
