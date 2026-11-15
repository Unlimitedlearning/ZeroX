import asyncio
import subprocess
import sys
import logging
import os
import json
import re
import shlex
import time
import datetime
from logging.handlers import RotatingFileHandler
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# ==========================================
# [全局变量区域]
# ==========================================
current_log_handle = None
current_process = None
TEST_LOG_FILE = None
WORK_JSON_FILE = None
CURRENT_BUG = None

# 新增：状态锁与计时器变量
IS_TASK_INITIALIZED = False
START_TIME = None
CURRENT_ROUND = 0
TOTAL_ATTEMPTS = 0

# ==========================================
# [跨平台] 运行环境检测
# ==========================================
IS_WINDOWS = sys.platform == "win32"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
BACKUP_ROOT = os.path.join(BASE_DIR, "backups")
LOG_FILE = os.path.join(LOG_DIR, "mcp_server.log")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# 基准测试结果文件
METRICS_FILE = os.path.join(LOG_DIR, "benchmark_metrics.jsonl")

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(BACKUP_ROOT, exist_ok=True)

handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8'
)
logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ==========================================
# 配置文件加载
# ==========================================
def load_config():
    """Fail Fast"""
    if not os.path.exists(CONFIG_FILE):
        logging.error(f"❌ Fatal: Configuration file '{CONFIG_FILE}' not found.")
        logging.error("👉 Please copy 'config.example.json' to 'config.json' and fill in your absolute paths.")
        sys.exit(1)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f_cfg:
            config = json.load(f_cfg)
    except Exception as err:
        logging.error(f"❌ Fatal: Failed to parse '{CONFIG_FILE}': {err}")
        sys.exit(1)

    # 校验必须的字段
    for key in ["defects4j_path", "work_root"]:
        if key not in config or not isinstance(config[key], str) or not config[key].strip():
            logging.error(f"❌ Fatal: Missing or empty '{key}' in '{CONFIG_FILE}'.")
            sys.exit(1)

    return config

CONFIG = load_config()
D4J_PATH = CONFIG["defects4j_path"]
WORK_ROOT = CONFIG["work_root"].rstrip("/")

logging.info(
    f"运行环境：{'Windows(WSL 转发)' if IS_WINDOWS else 'Linux(原生)'} | "
    f"defects4j: {D4J_PATH} | work_root: {WORK_ROOT}"
)

def build_workdir(project: str, version: str) -> str:
    return f"{WORK_ROOT}/d4j_{project}_{version}"


# ==========================================
# 【跨平台】统一命令执行器：上层只给 bash 命令，不再关心是 WSL 还是原生
# ==========================================
def shell_argv(cmd: str):
    """把一条 bash 命令包装成当前平台的 argv。"""
    return ["wsl", "bash", "-c", cmd] if IS_WINDOWS else ["bash", "-c", cmd]


def execute_in_target(cmd: str, check: bool = False, timeout=None):
    """同步执行一条 bash 命令（cat / cp / ...），返回 CompletedProcess。"""
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


def current_backup_dir():
    """按当前 Bug 隔离备份目录，形如 backups/Lang_1b。
    未初始化时落到 backups/None，保证 patch 与 rollback 两侧算出来的目录完全一致。
    """
    return os.path.join(BACKUP_ROOT, str(CURRENT_BUG))


def write_target_file(target_path: str, content: str):
    """把内容写进目标环境路径（target_path 是目标环境里的绝对路径）。
    Linux 原生：直接 open 写入，不经临时文件中转；
    Windows：保留「临时文件 + cp」逻辑，绕开 WSL 路径与编码/换行被改写的问题。
    """
    if not IS_WINDOWS:
        with open(target_path, "w", encoding="utf-8", newline="\n") as f_target:
            f_target.write(content)
        return

    # 临时文件名带 pid + 纳秒时间戳，避免并发或残留文件串味
    temp_win_path = os.path.join(LOG_DIR, f"temp_code_{os.getpid()}_{time.time_ns()}.txt")
    with open(temp_win_path, "w", encoding="utf-8", newline="\n") as f_temp:
        f_temp.write(content)

    cmd = f"cp {shlex.quote(win_path_to_wsl(temp_win_path))} {shlex.quote(target_path)}"
    try:
        execute_in_target(cmd, check=True, timeout=120)
    finally:
        # 临时文件只是中转，成功失败都不留
        try:
            os.remove(temp_win_path)
        except OSError:
            pass


def restore_written_files(applied_files, staged_writes):
    """【P0-2】用内存里的原始内容把已写入的文件强行还原。
    返回 (恢复成功列表, 恢复失败列表)；单个文件还原失败只记日志，不中断后续还原。
    """
    restored, broken = [], []
    for wsl_path in applied_files:
        try:
            write_target_file(wsl_path, staged_writes[wsl_path]["original"])
            restored.append(wsl_path)
        except Exception as err:
            logging.error(f"自动还原 {wsl_path} 失败: {str(err)}")
            broken.append(wsl_path)
    return restored, broken

# ==========================================
# 指标记录器（JSONL）
# ==========================================
def record_metric(status: str):
    global START_TIME, IS_TASK_INITIALIZED
    if START_TIME is None:
        return
    
    end_time = time.time()
    elapsed = round(end_time - START_TIME, 2)
    
    metric_data = {
        "timestamp": datetime.datetime.now().isoformat(),
        "bug_id": CURRENT_BUG,
        "status": status,
        "total_time_seconds": elapsed,
        "current_round": CURRENT_ROUND,      # 最终停留在第几轮
        "total_attempts": TOTAL_ATTEMPTS     # 真实的 API 调用/修改次数
    }
    
    with open(METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(metric_data, ensure_ascii=False) + "\n")
    
    logging.info(f"📊 记录基准数据: {metric_data}")
    
    # 记录完毕，重置计时器和锁状态，准备好下一个 Bug
    START_TIME = None
    IS_TASK_INITIALIZED = False

server = Server("zerox-server")

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="run_defects4j_test",
            description="后台运行 Defects4J 测试（包含 checkout 和 test），立刻返回。稍后用 read_test_log 查看结果。",
            inputSchema={
                "type": "object",
                "properties": {"bug_id": {"type": "string"}},
                "required": ["bug_id"]
            }
        ),
        types.Tool(
            name="read_test_log",
            description="读取 Defects4J 测试的实时日志",
            inputSchema={"type": "object", "properties": {}}
        ),
        types.Tool(
            name="read_code_from_wsl",
            description="读取 WSL 里的代码文件内容。支持指定行范围以节省上下文。",
            inputSchema={
                "type": "object",
                "properties": {
                    "wsl_file_path": {"type": "string"},
                    "start_line": {"type": "integer", "description": "可选，读取的起始行（从1开始）"},
                    "end_line": {"type": "integer", "description": "可选，读取的结束行"}
                },
                "required": ["wsl_file_path"]
            }
        ),
        types.Tool(
            name="patch_and_log",
            description="核心工具：将修改方案持久化到日志，并自动将代码 Diff 应用到目标文件中。每次修改代码必须调用此工具，严禁输出全量代码！",
            inputSchema={
                "type": "object",
                "properties": {
                    "round": {"type": "integer", "description": "第几轮修复"},
                    "status": {"type": "string", "enum": ["ANALYZING", "PATCH_GENERATED", "TEST_FAILED"], "description": "当前状态"},
                    "reasoning": {"type": "string", "description": "你对报错原因的推断（越精炼越好）"},
                    "fix_summary": {"type": "string", "description": "简明扼要说明你打算怎么改（50字以内）"},
                    "changes": {
                        "type": "array",
                        "description": "本轮修改涉及的文件和代码差异列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string", "description": "你修改了哪个文件"},
                                "patch_diff": {
                                    "type": "object",
                                    "properties": {
                                        "old": {"type": "string", "description": "被修改的原始代码片段（必须与文件精确匹配，包含缩进）"},
                                        "new": {"type": "string", "description": "修改后的新代码片段"}
                                    },
                                    "required": ["old", "new"]
                                }
                            },
                            "required": ["file_path", "patch_diff"]
                        }
                    }
                },
                "required": ["round", "status", "reasoning", "fix_summary", "changes"]
            }
        ),
        types.Tool(
            name="run_test_only",
            description="在已有的 workdir 中直接运行测试（不重新 checkout）。打完补丁后使用此工具验证。",
            inputSchema={
                "type": "object",
                "properties": {"workdir": {"type": "string"}},
                "required": ["workdir"]
            }
        ),
        types.Tool(
            name="get_work_log",
            description="读取你之前轮次的修复记录，用于回忆你做过哪些修改。",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "读取最近几轮的日志，默认3"}},
                "required": []
            }
        ),
        types.Tool(
            name="rollback",
            description="撤销你的修改。如果发现当前的修复方向完全错误，可以调用此工具回滚。",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_round": {
                        "type": "integer",
                        "description": "撤销到第几轮之前。传 1 表示撤销所有修改回到初始状态；传 2 表示撤销第 2 轮及之后的修改，保留第 1 轮。"
                    }
                },
                "required": ["target_round"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    global current_log_handle, current_process, WORK_JSON_FILE, TEST_LOG_FILE, CURRENT_BUG
    global IS_TASK_INITIALIZED, START_TIME, CURRENT_ROUND, TOTAL_ATTEMPTS 

    if name == "run_defects4j_test":
        # 🚨 防呆锁：只初始化一次
        if IS_TASK_INITIALIZED:
            return [types.TextContent(type="text", text=f"【系统强制拒绝】当前任务 `{CURRENT_BUG}` 的测试环境已初始化！严禁重复执行 checkout。如果你认为代码已被彻底改坏，请先调用 `rollback(target_round=1)` 重置代码，再继续修复。", isError=True)]
        
        if current_process is not None and current_process.poll() is None:
            return [types.TextContent(type="text", text="【系统拒绝】上一轮测试仍在运行中，请先调用 read_test_log 确认其状态，或等待其完成。", isError=True)]

        bug_id = arguments.get("bug_id", "Lang_1")

        # 校验格式，比如 Lang_1 或 Lang_1b
        match = re.match(r"^([A-Za-z]+)_(\d+)([bf]?)$", bug_id)
        if not match:
            return [types.TextContent(type="text", text="bug_id 格式错误，应为 Project_Number，如 Lang_1", isError=True)]

        project, bug_num, suffix = match.groups()
        version = bug_num + (suffix if suffix else "b") # 默认加 b

        # 【多 Bug 隔离】解析出 project/version 后立刻锁定当前 Bug，并切到该 Bug 专属的日志路径，
        # 这样子测完 Lang_1 再测 Lang_2，两边的测试日志和工作日志不会互相覆盖/追加污染
        CURRENT_BUG = f"{project}_{version}"
        TEST_LOG_FILE = os.path.join(LOG_DIR, f"test_{CURRENT_BUG}.log")
        WORK_JSON_FILE = os.path.join(LOG_DIR, f"work_log_{CURRENT_BUG}.json")
        logging.info(f"切换当前 Bug 为 {CURRENT_BUG} | 测试日志: {TEST_LOG_FILE} | 工作日志: {WORK_JSON_FILE}")

        workdir = build_workdir(project, version)
        d4j = shlex.quote(D4J_PATH)
        wsl_workdir = shlex.quote(workdir)

        full_cmd = f"rm -rf {wsl_workdir} && {d4j} checkout -p {project} -v {version} -w {wsl_workdir} && {d4j} test -w {wsl_workdir}"

        try:
            # 打开新的 TEST_LOG_FILE 之前，先把上一轮日志句柄安全关闭，防止句柄泄漏
            if current_log_handle is not None:
                try:
                    current_log_handle.close()
                except Exception:
                    pass
                current_log_handle = None

            f = open(TEST_LOG_FILE, "w", encoding="utf-8")
            current_log_handle = f
            current_process = popen_in_target(full_cmd, stdout=f, stderr=subprocess.STDOUT)

            # 计时器与状态锁更新
            IS_TASK_INITIALIZED = True
            START_TIME = time.time()
            CURRENT_ROUND = 0
            TOTAL_ATTEMPTS = 0

            logging.info("后台任务已启动")
            return [types.TextContent(type="text", text=f"任务已在后台启动！当前 Bug 为 `{CURRENT_BUG}`，workdir 是 `{workdir}`。请在 1-3 分钟后使用 `read_test_log` 工具查看执行结果。不要立刻重复执行！")]
        except Exception as e:
            logging.error(f"启动失败: {str(e)}")
            return [types.TextContent(type="text", text=f"启动失败: {str(e)}")]

    elif name == "read_test_log":

        if TEST_LOG_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_defects4j_test 初始化当前 Bug 的测试环境。", isError=True)]

        if not os.path.exists(TEST_LOG_FILE):
            return [types.TextContent(type="text", text="日志文件不存在，可能任务还没开始。")]

        # 进程状态机：先判断测试进程死活，再解析日志
        is_running = current_process is not None and current_process.poll() is None
        exit_code = None
        if current_process is not None and not is_running:
            exit_code = current_process.returncode

        # 状态 0：阻塞等待
        if is_running:
            try:
                # 最多等300秒，期间不消耗Token
                current_process.wait(timeout=300)
            except subprocess.TimeoutExpired:
                # ⏱️ 记录超时
                record_metric("Timeout")
                return [types.TextContent(type="text", text="【系统提示】测试已运行超过 5 分钟仍未结束，可能发生死锁或超时。建议检查环境或结束任务。")]
            
            # 更新状态
            is_running = False
            exit_code = current_process.returncode

        with open(TEST_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()


        if not content.strip():
            return [types.TextContent(type="text", text="【日志为空】测试还没有输出任何内容，请稍后再试。")]

        # 状态 1：服务重启后拿不到进程状态，退化为看日志关键字（兼容旧日志）
        if current_process is None and "Running ant" in content and "Failing tests:" not in content and "BUILD FAILED" not in content:
            return [types.TextContent(type="text", text="【系统提示】当前正在编译或运行测试中，请勿盲目猜测。请等待 1 分钟后再次调用此工具查看结果。")]

        # 状态 2：全部通过
        if "Failing tests: 0" in content:
            # ⏱️ 记录成功
            record_metric("Fixed")
            note = "" if exit_code in (None, 0) else f"（注意：进程退出码为 {exit_code}，可查看日志尾部确认是否有编译告警）"
            return [types.TextContent(type="text", text=f"【测试全部通过】该 Bug 已成功修复！请结束任务。{note}")]

        # 状态 2.5：编译/构建失败（进程已退出，且日志里没有测试失败清单）
        if exit_code not in (None, 0) and "Failing tests:" not in content:
            return [types.TextContent(type="text", text=f"【编译失败 / BUILD FAILED】测试进程退出码为 {exit_code}，且日志里没有测试失败清单，说明代码没通过编译。请根据下面的日志修复语法/类型错误：\n{content[-2500:]}")]

        # 状态 3：测试失败，提取失败列表
        if "Failing tests:" in content:
            
            match = re.search(r"Failing tests:\s*([1-9]\d*)([\s\S]*)", content, re.DOTALL)
            if match:
                failing_info = match.group(2).strip() if match.group(2) else "无详细堆栈"
                # 【修复】不超长就原样返回，超长才头部+尾部截断
                if len(failing_info) <= 2500:
                    combined = failing_info
                else:
                    lines = failing_info.split("\n")
                    head = "\n".join(lines[:15])
                    tail = failing_info[-2500:]
                    combined = head + "\n... [中间日志省略] ...\n" + tail

                return [types.TextContent(type="text", text=f"【测试失败】共 {match.group(1)} 个用例失败。\n以下是失败的测试用例信息：\n{combined}\n\n请结合报错信息进行分析。")]

        # 其他情况，返回尾部信息（带上退出码，方便区分「跑完了但没结果」与「还在跑」）
        code_note = f"（进程退出码 {exit_code}）" if exit_code is not None else ""
        return [types.TextContent(type="text", text=f"最新日志如下{code_note}:\n{content[-2000:]}")]

    elif name == "read_code_from_wsl":
        wsl_path = arguments.get("wsl_file_path")
        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")

        try:
            result = execute_in_target(f"cat {shlex.quote(wsl_path)}")
            if result.returncode != 0:
                return [types.TextContent(type="text", text=f"读取失败: {result.stderr}")]

            lines = result.stdout.splitlines()
            total_lines = len(lines)

            # 如果没传行号，返回全部（但警告大模型）
            if start_line is None or end_line is None:
                return [types.TextContent(type="text", text=f"【警告：正在读取全量文件，共 {total_lines} 行，可能很占上下文。建议下次指定 start_line 和 end_line】\n" + result.stdout)]

            # 切片（注意 Python 的 0-index 和越界处理）
            start_idx = max(0, start_line - 1)
            end_idx = min(total_lines, end_line)

            selected_lines = lines[start_idx:end_idx]

            # 带行号输出
            formatted_lines = [f"{start_idx + i + 1}: {line}" for i, line in enumerate(selected_lines)]

            return [types.TextContent(type="text", text=f"文件 {wsl_path} 第 {start_line}-{end_line} 行内容如下:\n" + "\n".join(formatted_lines))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"异常: {str(e)}")]

    elif name == "run_test_only":
        # 先初始化
        if TEST_LOG_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_defects4j_test 初始化当前 Bug 的测试环境。", isError=True)]

        if current_process is not None and current_process.poll() is None:
            return [types.TextContent(type="text", text="【系统拒绝】上一轮测试仍在运行中，请先调用 read_test_log 确认其状态。", isError=True)]

        workdir = arguments.get("workdir")
        if not workdir:
            return [types.TextContent(type="text", text=f"参数错误：必须提供 workdir（形如 {build_workdir('Lang', '2b')}）", isError=True)]

        d4j = shlex.quote(D4J_PATH)
        # 目录先校验再跑，顺便 quote 掉带空格的路径
        wsl_workdir = shlex.quote(workdir)
        # 【修复】用 {} 包起来，目录不存在时写入提示并 exit 1，不再往下执行
        full_cmd = f"test -d {wsl_workdir} || {{ echo 'WORKDIR_NOT_FOUND: 指定的目录不存在'; exit 1; }}; {d4j} test -w {wsl_workdir}"

        try:
            # 打开新的 TEST_LOG_FILE 之前，先把上一轮日志句柄安全关闭，防止句柄泄漏
            if current_log_handle is not None:
                try:
                    current_log_handle.close()
                except Exception:
                    pass
                current_log_handle = None

            f = open(TEST_LOG_FILE, "w", encoding="utf-8")
            current_log_handle = f
            current_process = popen_in_target(full_cmd, stdout=f, stderr=subprocess.STDOUT)
            return [types.TextContent(type="text", text=f"已在当前 Bug（{CURRENT_BUG}）的 workdir 启动测试！请稍后读取日志。（目录已存在性校验：{workdir}）")]
        except Exception as e:
            return [types.TextContent(type="text", text=f"启动失败: {str(e)}")]

    # ==========================================
    # 下面是合并后的核心工具：记录日志 + 应用补丁
    # ==========================================
    elif name == "patch_and_log":
        # 先初始化
        if WORK_JSON_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_defects4j_test 初始化当前 Bug 的测试环境。", isError=True)]

        round_num = arguments.get("round")
        status = arguments.get("status")
        reasoning = arguments.get("reasoning")
        fix_summary = arguments.get("fix_summary")
        changes = arguments.get("changes", [])

        # 第一阶段：全部在内存中校验和替换，order 用来保持落盘顺序。
        staged_writes = {}
        order = []

        for change in changes:
            wsl_path = change.get("file_path")
            patch_diff = change.get("patch_diff", {})
            old_str = patch_diff.get("old", "")
            new_str = patch_diff.get("new", "")

            if not wsl_path or not old_str:
                return [types.TextContent(type="text", text="参数错误：file_path 或 old 不能为空。", isError=True)]

            try:
                # 只有首次遇到这个文件才回读磁盘，后续直接基于内存里的最新内容继续替换
                if wsl_path not in staged_writes:
                    res = execute_in_target(f"cat {shlex.quote(wsl_path)}")
                    if res.returncode != 0:
                        return [types.TextContent(type="text", text=f"修改失败：找不到文件 {wsl_path}", isError=True)]

                    content = res.stdout.replace("\r\n", "\n") # 顺手解决 CRLF 问题
                    staged_writes[wsl_path] = {"content": content, "original": res.stdout} # original 留给备份和失败还原
                    order.append(wsl_path)

                content = staged_writes[wsl_path]["content"]
                old_str_normalized = old_str.replace("\r\n", "\n")

                count = content.count(old_str_normalized)
                if count == 0:
                    return [types.TextContent(type="text", text=f"修改失败：{wsl_path} 中找不到 old 片段。请重新核对缩进和代码内容。", isError=True)]
                elif count > 1:
                    return [types.TextContent(type="text", text=f"修改失败：old 片段在 {wsl_path} 中出现了 {count} 次，请提供更长的上下文。", isError=True)]

                # 逐段叠加到内存里，本轮结束时只写盘一次
                staged_writes[wsl_path]["content"] = content.replace(old_str_normalized, new_str, 1)

            except Exception as e:
                return [types.TextContent(type="text", text=f"代码校验发生异常: {str(e)}", isError=True)]

        # 第二阶段：确认所有文件都能改，才开始统一落盘
        applied_files = []

        # 备份按 Bug 分目录：backups/Lang_1b/、backups/Lang_2b/ ...
        backup_dir = current_backup_dir()
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)

        try:
            # 每个文件只写一次
            for wsl_path in order:
                new_content = staged_writes[wsl_path]["content"]
                original_content = staged_writes[wsl_path]["original"]

                # 写入前，先保存备份，用完整路径替换斜杠来命名，防止不同目录的同名文件互相覆盖
                safe_name = wsl_path.replace("/", "_").replace(":", "_")
                backup_filename = f"{safe_name}_round_{round_num}.bak"
                backup_win_path = os.path.join(backup_dir, backup_filename)
                with open(backup_win_path, "w", encoding="utf-8", newline="\n") as f_bak:
                    f_bak.write(original_content)
                
                write_target_file(wsl_path, new_content)
                applied_files.append(wsl_path)
        except Exception as e:
            # 写盘中途失败：立刻用内存里的原文把已写入的文件强行还原，
            # 避免出现「工具说失败、代码却半改、日志里也查不到」的静默脏状态
            restored, broken = restore_written_files(applied_files, staged_writes)
            msg = f"写入硬盘时发生异常：{str(e)}。已自动回滚 {len(restored)} 个文件"
            if broken:
                msg += f"，但 {len(broken)} 个文件还原失败（{', '.join(broken)}），请用 read_code_from_wsl 手动检查"
            msg += "。本轮修改未写入工作日志。"
            return [types.TextContent(type="text", text=msg, isError=True)]

        # 第三阶段：代码成功写入，再写日志
        log_data = {
            "timestamp": datetime.datetime.now().isoformat(),
            "round": round_num,
            "bug": CURRENT_BUG,
            "status": status,
            "reasoning": reasoning,
            "fix_summary": fix_summary,
            "changes": changes
        }

        with open(WORK_JSON_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_data, ensure_ascii=False) + "\n")

        # 更新全局轮次变量，供指标记录使用
        CURRENT_ROUND = round_num
        
        TOTAL_ATTEMPTS += 1
        return [types.TextContent(type="text", text=f"修复成功！代码差异已原子化应用到 {len(applied_files)} 个文件，工作日志（{os.path.basename(WORK_JSON_FILE)}）已持久化。现在你可以调用 run_test_only 跑测试了。")]

    elif name == "get_work_log":
        # 【多 Bug 隔离】只读当前 Bug 的工作日志
        if WORK_JSON_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_defects4j_test 初始化当前 Bug 的测试环境。", isError=True)]

        limit = arguments.get("limit", 3)

        if limit <= 0:
            return [types.TextContent(type="text", text="已跳过读取历史日志（limit <= 0）。")]

        if not os.path.exists(WORK_JSON_FILE):
            return [types.TextContent(type="text", text=f"当前 Bug（{CURRENT_BUG}）暂无历史工作日志。")]

        with open(WORK_JSON_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        recent_logs = lines[-limit:]
        return [types.TextContent(type="text", text=f"当前 Bug（{CURRENT_BUG}）最近的工作日志如下：\n" + "".join(recent_logs))]

    elif name == "rollback":

        if WORK_JSON_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_defects4j_test 初始化当前 Bug 的测试环境。", isError=True)]

        target_round = arguments.get("target_round")
        if target_round is None:
            return [types.TextContent(type="text", text="参数错误：必须提供 target_round", isError=True)]

        # 【多 Bug 隔离】备份目录同样按 CURRENT_BUG 隔离，与 patch_and_log 写入侧保持同一算法
        backup_dir = current_backup_dir()
        if not os.path.exists(backup_dir):
            return [types.TextContent(type="text", text=f"当前 Bug（{CURRENT_BUG}）没有找到任何备份文件，无法回滚。", isError=True)]

        if not os.path.exists(WORK_JSON_FILE):
            return [types.TextContent(type="text", text=f"当前 Bug（{CURRENT_BUG}）没有历史日志，无需回滚。")]

        with open(WORK_JSON_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        restored_files = set()

        # 倒序遍历日志，找到所有 >= target_round 的修改
        for line in reversed(lines):
            try:
                log_data = json.loads(line)
            except json.JSONDecodeError:
                continue # 跳过坏行

            r = log_data.get("round")
            if r is not None and r >= target_round:
                changes = log_data.get("changes", [])
                for change in changes:
                    wsl_path = change.get("file_path")
                    if not wsl_path: continue

                    # 寻找对应的备份文件，r为当前循环的轮次 
                    safe_name = wsl_path.replace("/", "_").replace(":", "_")
                    backup_filename = f"{safe_name}_round_{r}.bak"
                    backup_win_path = os.path.join(backup_dir, backup_filename)

                    if os.path.exists(backup_win_path):
                        # 将备份内容写回原路径
                        try:
                            with open(backup_win_path, "r", encoding="utf-8") as f_bak:
                                backup_content = f_bak.read()
                            write_target_file(wsl_path, backup_content)
                            restored_files.add(wsl_path)
                            os.remove(backup_win_path) # 删除备份，防止重复回滚。
                        except Exception as e:
                            logging.error(f"恢复文件 {wsl_path} 失败: {str(e)}")
                            # 记录失败但继续恢复下一个文件

        # 先判断是否真正恢复文件：一个都没恢复就绝不动工作日志，
        # 否则会出现「日志被删干净、代码还是脏的、以后也没法回滚」的最坏情况
        if not restored_files:
            return [types.TextContent(type="text", text=f"在第 {target_round} 轮及之后没有找到可用的备份文件，未改动任何代码和日志。")]

        # 截断工作日志，删除 >= target_round 的日志
        new_lines = []
        for line in lines:
            if not line.strip(): continue
            try:
                log_data = json.loads(line)
            except json.JSONDecodeError:
                new_lines.append(line) # 保留坏行或跳过，建议保留以防丢失数据
                continue
            r = log_data.get("round")
            if r is None or r < target_round:
                new_lines.append(line)

        with open(WORK_JSON_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        return [types.TextContent(type="text", text=f"回滚成功！已撤销当前 Bug（{CURRENT_BUG}）第 {target_round} 轮及之后的修改，恢复了 {len(restored_files)} 个文件到修改前的状态。你可以调用 get_work_log 确认当前的历史状态。")]

async def main():
    logging.info("🔥 MCP Server 启动！")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
