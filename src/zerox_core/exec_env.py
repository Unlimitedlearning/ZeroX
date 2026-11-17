"""目标环境命令执行器：把 WSL 转发 / 原生 bash 的差异收在一个地方。

这里只做「拼 argv + 起进程」，不持有任何运行期状态，所以事后审计（audit.py）
也能用同一条 test_cmd 复跑，不必再抄一份跨平台转发逻辑。
"""
import os
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"


def shell_argv(cmd: str):
    """把一条 bash 命令包装成当前平台的 argv。"""
    return ["wsl", "bash", "-c", cmd] if IS_WINDOWS else ["bash", "-c", cmd]


def execute_in_target(cmd: str, check: bool = False, timeout=None):
    """同步执行一条 bash 命令（cat / cp / git ...），返回 CompletedProcess。"""
    return subprocess.run(
        shell_argv(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
        timeout=timeout
    )


def popen_in_target(cmd: str, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT):
    """后台执行一条 bash 命令（Defects4J checkout / test），返回 Popen 句柄。"""
    return subprocess.Popen(
        shell_argv(cmd),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr
    )


def win_path_to_wsl(win_path: str) -> str:
    """Windows 绝对路径 → WSL 路径（D:\\foo → /mnt/d/foo），任意盘符通用。"""
    posix_path = os.path.abspath(win_path).replace("\\", "/")
    if len(posix_path) > 1 and posix_path[1] == ":":
        return f"/mnt/{posix_path[0].lower()}{posix_path[2:]}"
    return posix_path
