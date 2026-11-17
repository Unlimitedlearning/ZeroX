import asyncio
import subprocess
import logging
import os
import re
import shlex
import fnmatch
import posixpath
import time
from logging.handlers import RotatingFileHandler
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# 项目内模块（解耦第一步：配置与工具 schema 已外迁）
from zerox_core import config as config_mod
from zerox_core import schemas
from zerox_core.config import compile_or_fail
from zerox_core.config import resolve_bug as _resolve_bug
# 命令执行器已外迁：事后审计（audit.py）要用同一条 test_cmd 复跑，不能各写一份 WSL 转发逻辑
from zerox_core.exec_env import IS_WINDOWS, execute_in_target, popen_in_target, win_path_to_wsl
# 外置版本库：work_log 事件流 / blob 快照 / 回滚（独立模块，不依赖本文件任何东西）
import worklog

# ==========================================
# [全局变量区域]
# ==========================================
current_log_handle = None
current_process = None
TEST_LOG_FILE = None
WORK_JSON_FILE = None
CURRENT_BUG = None
# 当前 bug 的工作区（由 config.json 的 target.workspace_template 解析而来）
CURRENT_WORKDIR = None

# 新增：状态锁与计时器变量
IS_TASK_INITIALIZED = False
START_TIME = None
CURRENT_ROUND = 0
# 累计补丁数：只用于指标与预算读数，不再作为任务终止条件
TOTAL_ATTEMPTS = 0
# 累计测试次数：本任务真正的成本单位（一次测试 3-6 分钟），也是唯一的硬闸门
TOTAL_TESTS = 0
READ_CODE_COUNT = 0

# ==========================================
# [跨平台] 运行环境检测（IS_WINDOWS 跟随执行器一起外迁到 src/zerox_core/exec_env.py）
# ==========================================
# 代码在 src/，而 config.json / logs/ 在项目根目录：BASE_DIR 是本文件目录，PROJECT_ROOT 是项目根
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
LOG_FILE = os.path.join(LOG_DIR, "mcp_server.log")
# 配置文件：Agent（src/zerox.py）会通过 ZEROX_CONFIG 传进来，保证客户端/服务端读同一份
CONFIG_FILE = os.getenv("ZEROX_CONFIG") or os.path.join(PROJECT_ROOT, "config.json")

# 基准测试指标（logs/benchmark_metrics.jsonl）由 Agent（src/zerox.py）负责写入：
# token 用量只有调用 LLM 的客户端才知道，服务端这一侧只留耗时日志

os.makedirs(LOG_DIR, exist_ok=True)

handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8'
)
logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)





# ==========================================
# 配置加载：默认值 / 校验 / 正则编译都在 src/zerox_core/config.py
# ==========================================
CONFIG = config_mod.load_config(CONFIG_FILE)
TARGET = config_mod.load_target_config(CONFIG, CONFIG_FILE)
AGENT_CONFIG = config_mod.load_agent_config(CONFIG, CONFIG_FILE)
TOOL_PATH = TARGET["tool_path"]
WORKSPACE_ROOT = (TARGET["workspace_root"] or "").rstrip("/")
# 预算单位是「测试次数」：一个 patch 只花几 KB 上下文，一次测试要 3-6 分钟。
# 所以要限制的是昂贵的那个动作，patch 本身不限次（编译错误修复也算 patch，不该吃预算）。
MAX_TESTS = AGENT_CONFIG.get("max_tests", 12)
TEST_TIMEOUT = CONFIG["timeouts"]["test_execution"]

# 判定规则来自 config.json（换测试集不用改代码）；bug_id 的解析在 src/zerox_core/config.py
PASS_RE = compile_or_fail(TARGET["passed_pattern"], "target.passed_pattern")
FAIL_RE = compile_or_fail(TARGET["failed_pattern"], "target.failed_pattern")

# test_cmd 不能为空；命令里用了 {tool} 就必须配 tool_path
config_mod.validate_or_exit(CONFIG, TARGET, TOOL_PATH, CONFIG_FILE)

logging.info(
    f"运行环境：{'Windows(WSL 转发)' if IS_WINDOWS else 'Linux(原生)'} | "
    f"tool: {TOOL_PATH or '(未配置)'} | workspace_root: {WORKSPACE_ROOT}"
)


def resolve_bug(bug_id: str):
    """把 bug_id 解析成「工作区 + 命令」一整套配置；不符合 target.bug_id_pattern 就返回 None。

    实现已外迁到 src/zerox_core/config.py（纯函数）：事后审计 audit.py 要用同一套解析，
    才能拿到同一个工作区路径和同一条 test_cmd。
    """
    return _resolve_bug(TARGET, bug_id)


# ==========================================
# 【路径约束】默认只拦「写不进当前工作区」的 patch
# 以前这里还有两层防作弊：只能改 src/main/java 的白名单，加上跑测试前比对的测试文件 md5 基线。
# 两层都拿掉了，原因有二：白名单把 src/main/java/... 这种相对路径也拒掉了（模型经常给相对路径），
# md5 基线换个项目布局就得额外配 4 个字段才不误判；而被拒的 agent 会转为先随便打个补丁换权限，
# 赔进去的就是一次 3-6 分钟的测试。现在运行时只管「别写到工作区外面」，
# 动没动测试代码交给事后的 audit.py 判定：Fixed 和 Verified Fixed 分开报。
# patch_allow_globs / patch_deny_globs 仍然保留（默认空），需要时再收紧。
# ==========================================
def _glob_list(value):
    """把配置里的白名单/黑名单统一成 list[str]；空值 = 不限制。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value if str(v).strip()]


def match_any_glob(path: str, globs) -> bool:
    """fnmatch 逐个比对（支持 */src/test/* 这类中间段通配）。

    必须用 fnmatchcase 而不能用 fnmatch：Windows 上 fnmatch 会先 normcase 转小写，
    于是 D:\\...\\Test\\ 里的 “Test” 会命中 */test/*，把整个工作区误判成测试目录。
    """
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def normalize_patch_path(raw_path: str) -> str:
    """把模型给的路径统一成工作区内的绝对路径。

    模型经常给 src/main/java/... 这种相对路径，直接拿去 cat / 与工作区前缀比对都会对不上，
    旧的白名单就是这么把合法 patch 拒掉的（日志里那条
    “src/main/java/.../NumberUtils.java 不在 patch_allow_globs 白名单内”就是它）。
    """
    path = str(raw_path or "").strip().replace("\\", "/")
    if not path:
        return ""
    workdir = (CURRENT_WORKDIR or "").rstrip("/")
    if workdir and not path.startswith("/"):
        path = f"{workdir}/{path}"
    return posixpath.normpath(path)


def check_patch_path(wsl_path: str):
    """判断这个路径能否被 patch，返回 (ok, 拒绝原因)。

    三层判定，任一不通过就拒绝（白/黑名单默认都为空，所以平时只有第 3 层生效）：
      1. 命中 patch_deny_globs（受保护文件）→ 拒绝
      2. 有白名单但没命中 patch_allow_globs → 拒绝
      3. patch_must_be_under_workspace 为真时，路径必须落在当前工作区内
    """
    path = str(wsl_path or "").replace("\\", "/")
    if not path:
        return False, "file_path 为空"
    # 先杀掉路径穿越：/ws/src/main/java/../../test/X.java 能命中白名单，实际写到测试目录
    if ".." in path.split("/"):
        return False, f"{path} 含 '..'（路径穿越），已拒绝"
    path = posixpath.normpath(path)

    allow = _glob_list(TARGET.get("patch_allow_globs"))
    deny = _glob_list(TARGET.get("patch_deny_globs"))

    if deny and match_any_glob(path, deny):
        return False, f"{path} 命中 patch_deny_globs（测试代码/受保护文件禁止修改）"
    if allow and not match_any_glob(path, allow):
        return False, f"{path} 不在 patch_allow_globs 白名单内（允许：{', '.join(allow)}）"

    workdir = (CURRENT_WORKDIR or "").rstrip("/")
    if workdir and TARGET.get("patch_must_be_under_workspace", True):
        if not (path == workdir or path.startswith(workdir + "/")):
            return False, f"{path} 不在当前工作区 {workdir} 内"
    return True, ""


# ==========================================
# 【预算读数】只报不管
# 以前这里是一整层「调用仲裁」：重复只读直接省略、连续 12 次只读直接拒绝、
# 任务进行中强行把 force_checkout 降级。它挡掉的都是几秒钟的只读，
# 却把 agent 逼成「为了换到读权限先随便打一个补丁」——赔进去的是一次 3-6 分钟的测试。
# 现在改成：把事实告诉它，由它自己决定。唯一的硬闸门是测试次数（见 run_test）。
# ==========================================
def budget_line() -> str:
    """一行预算读数，附在 run_test / patch_and_log 的返回末尾。"""
    left = max(0, MAX_TESTS - TOTAL_TESTS)
    return (
        f"\n\n[预算] 测试 {TOTAL_TESTS}/{MAX_TESTS}（还剩 {left} 次）"
        f" · 补丁 {TOTAL_ATTEMPTS} 个 · 本轮已读代码 {READ_CODE_COUNT} 次"
    )


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


# ==========================================
# 任务结束复位（指标由 Agent src/zerox.py 记录）
# ==========================================
def reset_task_state(status: str):
    """一次任务走到终点（测试通过 / 超时 / 达到最大尝试次数）后复位状态锁。

    基准指标（含 token 用量、轮次、耗时）由 src/zerox.py 汇总后写入
    logs/benchmark_metrics.jsonl；服务端只留一条耗时日志，方便对照 mcp_server.log 排查。
    """
    global START_TIME, IS_TASK_INITIALIZED
    if START_TIME is not None:
        logging.info(f"⏱ 任务结束（{status}）：{CURRENT_BUG} 耗时 {round(time.time() - START_TIME, 2)}s，"
                     f"第 {CURRENT_ROUND} 轮，累计 {TOTAL_ATTEMPTS} 次修改")
    
    # 复位计时器和锁状态，准备好下一个 Bug
    START_TIME = None
    IS_TASK_INITIALIZED = False


# ==========================================
# 【run_test 结果回写】每条结论追加一条 test 事件，永不删除
# 旧实现只保留最新一次测试结果（每次先把所有历史 test_result 清掉），等于删掉了
# agent 最需要的那条信号：失败数是在收敛还是在发散。现在失败数、详情都留在 work_log 里，
# get_work_log 会渲染成「上轮 5 个 → 2 个」这种对比，agent 才知道该微调还是该换方向。
# ==========================================
def save_test_result(test_status: str, fail_count=None, detail: str = ""):
    """把最近一次 run_test 的结论追加到 work_log（worklog.test）。

    落盘异常只记日志，绝不打断 run_test 的返回。
    """
    wl = _worklog()
    if wl is None:
        return
    try:
        wl.test(CURRENT_ROUND, test_status, fail_count, detail)
    except Exception as err:
        logging.error(f"写入 run_test 结果失败: {str(err)}")


def extract_fail_info(match):
    """从失败正则里取「失败数」和「详细堆栈」。

    优先用命名分组 count / detail；没有命名分组就退回 group(1) / group(2)，
    方便随手写的正则也能用；两个分组都可以省略。
    """
    groups = match.groupdict()
    count = groups.get("count")
    detail = groups.get("detail")

    if count is None and match.groups():
        count = match.group(1)
    if detail is None and match.lastindex and match.lastindex >= 2:
        detail = match.group(2)

    fail_count = int(count) if isinstance(count, str) and count.isdigit() else None
    return fail_count, (detail or "").strip()


def wait_for_previous_process():
    """统一阻塞等待函数：如果有测试在跑，最多等TEST_TIMEOUT秒。"""
    global current_process
    if current_process is not None and current_process.poll() is None:
        try:
            # 使用配置中的超时时间
            current_process.wait(timeout=TEST_TIMEOUT)
        except subprocess.TimeoutExpired:
            logging.warning(f"测试进程超时（>{TEST_TIMEOUT}秒），准备强制终止。")
            current_process.kill()

# ==========================================
# 【外置状态机】worklog 的 IO 适配层
# 目标工作区在 WSL（或本地 bash）里，不是本机路径：read / write / remove 全走 Target 环境
# ==========================================
class TargetIO:
    """worklog 用它在目标环境里读写文件；本地原生执行时同样适用。"""

    def read(self, path):
        res = execute_in_target(f"cat {shlex.quote(path)}")
        if res.returncode != 0:
            raise FileNotFoundError(path)
        return res.stdout

    def write(self, path, content):
        write_target_file(path, content)

    def remove(self, path):
        execute_in_target(f"rm -f {shlex.quote(path)}")


def _worklog():
    """按当前 Bug 打开它的版本库；还没进入任务时返回 None，由调用方拒绝。"""
    if not WORK_JSON_FILE:
        return None
    return worklog.WorkLog(os.path.dirname(WORK_JSON_FILE), io=TargetIO())


server = Server("zerox-server")

@server.list_tools()
async def list_tools():
    # schema 是纯数据，已外迁到 src/zerox_core/schemas.py
    return schemas.build_tools()

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    global current_log_handle, current_process, WORK_JSON_FILE, TEST_LOG_FILE, CURRENT_BUG
    global IS_TASK_INITIALIZED, START_TIME, CURRENT_ROUND, TOTAL_ATTEMPTS, TOTAL_TESTS
    global READ_CODE_COUNT, CURRENT_WORKDIR

    # 🚨 绝杀：每次调用任何工具前，强制等待上一次的测试进程跑完（防并发冲突）
    wait_for_previous_process()

    if name == "run_test":
        bug_id = arguments.get("bug_id", "Lang_1")
        force_checkout = arguments.get("force_checkout", False)

        # 校验格式：解析规则来自 config.json 的 target.bug_id_pattern
        bug_ctx = resolve_bug(bug_id)
        if bug_ctx is None:
            return [types.TextContent(type="text", text=f"bug_id 格式错误，应符合 config.json 中 target.bug_id_pattern 的规则：{TARGET['bug_id_pattern']}", isError=True)]

        new_bug_id = bug_ctx["bug_id"]
        workspace = bug_ctx["workspace"]
        setup_cmd = bug_ctx["setup_cmd"]
        test_cmd = bug_ctx["test_cmd"]

        # 【预算闸门】一次测试 3-6 分钟，跑满就结束 —— 这是唯一会真正拖住任务的地方。
        # 放在 checkout 之前有两个理由：
        #   1. 预算已经用尽时，没必要再花 2-3 分钟准备一个不会跑测试的工作区；
        #   2. 只有「换了 bug」才算新任务并复位 TOTAL_TESTS。否则 agent 自己传一个
        #      force_checkout=true 就能把 12 次预算重置成满额，这道闸门就形同虚设。
        is_new_bug = CURRENT_BUG != new_bug_id
        if not is_new_bug and TOTAL_TESTS >= MAX_TESTS:
            reset_task_state("MaxTests")
            return [types.TextContent(type="text", text=(
                f"【任务失败】已达到最大测试次数（{MAX_TESTS} 次）。请立刻停止修复并输出 [FAILED]。\n"
                f"本任务共提交 {TOTAL_ATTEMPTS} 个补丁。"
            ), isError=True)]
        
        # ---- 是否需要重新 checkout ----
        # 唯一的原则：run_test 只在「明确要求」或「确实是新任务」时重置工作区，
        # 绝不在任务中途悄悄 checkout —— 全量 checkout 要 2-3 分钟，还会把已打好的补丁打回 buggy。
        # （旧逻辑把 not IS_TASK_INITIALIZED 也算进去，而 reset_task_state 会把这个标志置 False，
        #   于是超时/中断之后的下一次 run_test 就把补丁冲掉了，现象就是「卡在 run_test 永远同一个报错」。）
        need_checkout = bool(force_checkout)

        if not need_checkout:
            if CURRENT_BUG != new_bug_id:
                if TOTAL_ATTEMPTS > 0:
                    # 带着未验证的补丁换 Bug：重置会直接把它丢掉，让 agent 自己做决定
                    return [types.TextContent(type="text", text=(
                        f"【拒绝切换】当前工作区属于 {CURRENT_BUG}，还有 {TOTAL_ATTEMPTS} 个未收敛的补丁"
                        f"（{new_bug_id} 是另一个 Bug）。切换会全量重置并丢弃这些改动。\n"
                        f"要继续 {new_bug_id}，请显式传 force_checkout=true；要留在 {CURRENT_BUG}，请传正确的 bug_id。"
                    ), isError=True)]
                need_checkout = True
            elif not IS_TASK_INITIALIZED and TOTAL_ATTEMPTS == 0:
                # 例如服务重启 / 上一轮被中断：工作区状态不可信，但还没有任何补丁，重新准备是安全的
                need_checkout = True

        wsl_workdir = shlex.quote(workspace)

        if need_checkout:
            # 【重置多 Bug 隔离环境】
            CURRENT_BUG = new_bug_id
            CURRENT_WORKDIR = workspace
            CURRENT_BUG_DIR = os.path.join(LOG_DIR, CURRENT_BUG)
            os.makedirs(CURRENT_BUG_DIR, exist_ok=True)
            TEST_LOG_FILE = os.path.join(CURRENT_BUG_DIR, "test.log")
            WORK_JSON_FILE = os.path.join(CURRENT_BUG_DIR, "work_log.jsonl")
            
            # 【新一轮任务】工作区刚被重置，旧的 work_log 里的轮次/补丁已经对不上当前代码，
            # 留着只会误导 agent（和指标里的 current_round），直接清掉
            if os.path.exists(WORK_JSON_FILE):
                try:
                    os.remove(WORK_JSON_FILE)
                    logging.info(f"已清空 {CURRENT_BUG} 的历史 work_log（工作区被重新检出）")
                except OSError as err:
                    logging.error(f"清空 work_log 失败: {str(err)}")
            
            # 重置锁和计时器
            IS_TASK_INITIALIZED = True
            START_TIME = time.time()
            CURRENT_ROUND = 0
            TOTAL_ATTEMPTS = 0
            # 只有新 bug 才复位测试预算：同一个 bug 上的 force_checkout 不能当「续命」用
            if is_new_bug:
                TOTAL_TESTS = 0
            READ_CODE_COUNT = 0
            
            logging.info(f"切换当前 Bug 为 {CURRENT_BUG} | 执行全量 Checkout")

            # setup_cmd 与 test_cmd 仍然分两步跑：准备阶段失败时，不能拿半成品工作区去跑增量测试。
            # setup_cmd 为空（工作区已存在、无需重置）时直接跳过准备阶段。
            if setup_cmd:
                try:
                    setup_res = execute_in_target(setup_cmd, timeout=TEST_TIMEOUT)
                except subprocess.TimeoutExpired:
                    msg = f"【环境准备超时】setup_cmd 超过 {TEST_TIMEOUT}s 未结束，已放弃本轮。"
                    save_test_result("SetupFailed", None, msg)
                    # 工作区可能是半成品：标回未初始化，下次 run_test 重新 checkout
                    IS_TASK_INITIALIZED = False
                    return [types.TextContent(type="text", text=msg, isError=True)]
                if setup_res.returncode != 0:
                    setup_out = ((setup_res.stdout or "") + (setup_res.stderr or "")).strip()
                    msg = f"【环境准备失败】setup_cmd 退出码 {setup_res.returncode}：\n{setup_out[-2000:]}"
                    save_test_result("SetupFailed", None, msg)
                    # 同上：别拿一个损坏的工作区继续跑增量测试
                    IS_TASK_INITIALIZED = False
                    return [types.TextContent(type="text", text=msg, isError=True)]

            full_cmd = test_cmd

        else:
            logging.info(f"当前 Bug {CURRENT_BUG} | 执行增量测试")
            # 工作区可能就在当前目录/固定目录，所以 require_workspace 做成可关的
            if TARGET["require_workspace"]:
                full_cmd = f"test -d {wsl_workdir} || {{ echo 'WORKDIR_NOT_FOUND: 指定的目录不存在'; exit 1; }}; {test_cmd}"
            else:
                full_cmd = test_cmd


        TOTAL_TESTS += 1

        try:
            if current_log_handle is not None:
                try:
                    current_log_handle.close()
                except Exception:
                    pass
                current_log_handle = None

            f = open(TEST_LOG_FILE, "w", encoding="utf-8")
            current_log_handle = f
            current_process = popen_in_target(full_cmd, stdout=f, stderr=subprocess.STDOUT)

            logging.info(f"开始阻塞等待测试进程结束（超时限制 {TEST_TIMEOUT}s）...")
            try:
                current_process.wait(timeout=TEST_TIMEOUT)
            except subprocess.TimeoutExpired:
                current_process.kill()
                save_test_result("Timeout", None, f"【测试超时】执行超过 {TEST_TIMEOUT} 秒仍未结束，进程已被强制终止。")
                reset_task_state("Timeout")
                # 超时不能把补丁一起丢掉：保持「已检出 + 已修改」，下一次 run_test 走增量而不是重新 checkout
                IS_TASK_INITIALIZED = True
                return [types.TextContent(type="text", text=f"【测试超时】执行超过 {TEST_TIMEOUT} 秒仍未结束，进程已被强制终止。" + budget_line(), isError=True)]

            logging.info("测试进程结束，正在解析日志...")
            
            # 读取并解析日志
            if not os.path.exists(TEST_LOG_FILE):
                save_test_result("Unknown", None, "测试完成，但日志文件不存在。")
                return [types.TextContent(type="text", text="测试完成，但日志文件不存在。", isError=True)]
            
            with open(TEST_LOG_FILE, "r", encoding="utf-8", errors="replace") as log_f:
                content = log_f.read()

            if not content.strip():
                save_test_result("EmptyLog", None, "【日志为空】测试没有任何输出。")
                return [types.TextContent(type="text", text="【日志为空】测试没有任何输出。")]

            exit_code = current_process.returncode

            # 【可配置判定】通过/失败规则来自 config.json 的 target.passed_pattern / failed_pattern
            if PASS_RE.search(content):
                save_test_result("Passed", 0, "【测试全部通过】命中 passed_pattern")
                reset_task_state("Fixed")
                return [types.TextContent(type="text", text=f"【测试全部通过】该 Bug 已成功修复！请结束任务并输出 [FIXED]。")]

            fail_match = FAIL_RE.search(content)
            if exit_code not in (None, 0) and fail_match is None:
                compile_msg = f"【编译失败】退出码 {exit_code}。请根据日志修复错误：\n{content[-2500:]}"
                save_test_result("CompileError", None, compile_msg)
                return [types.TextContent(type="text", text=compile_msg)]

            if fail_match is not None:
                fail_count, failing_info = extract_fail_info(fail_match)
                if len(failing_info) > 2500:
                    lines = failing_info.split("\n")
                    combined = "\n".join(lines[:15]) + "\n... [省略] ...\n" + failing_info[-2500:]
                else:
                    combined = failing_info or "无详细堆栈"
                count_text = fail_count if fail_count is not None else "若干"
                fail_msg = f"【测试失败】共 {count_text} 个用例失败。\n以下是报错信息：\n{combined}\n\n请分析并修复。"
                save_test_result("Failed", fail_count, fail_msg)
                return [types.TextContent(type="text", text=fail_msg + budget_line())]

            tail_msg = f"最新日志如下（退出码 {exit_code}）:\n{content[-2000:]}"
            save_test_result("Unknown", None, tail_msg)
            return [types.TextContent(type="text", text=tail_msg)]

        except Exception as e:
            logging.error(f"启动失败: {str(e)}")
            save_test_result("Error", None, f"测试执行异常: {str(e)}")
            return [types.TextContent(type="text", text=f"测试执行异常: {str(e)}", isError=True)]

    elif name == "search_code":
        if CURRENT_BUG is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_test 初始化 Bug 环境。", isError=True)]
        
        keyword = arguments.get("keyword")
        pattern = arguments.get("file_pattern", TARGET["source_glob"])
        
        # 工作区不再按 Defects4J 命名推导，统一用 resolve_bug 解析出来的 CURRENT_WORKDIR
        workdir = CURRENT_WORKDIR
        if not workdir:
            return [types.TextContent(type="text", text="【系统拒绝】还没解析出当前工作区，请先调用 run_test 初始化。", isError=True)]
        
        cmd = f"grep -rn --include='{pattern}' {shlex.quote(keyword)} {shlex.quote(workdir)} | head -n 20"
        res = execute_in_target(cmd)
        
        if res.returncode != 0 or not res.stdout.strip():
            return [types.TextContent(type="text", text=f"未找到关键字 '{keyword}'。请换个词或直接读取文件。")]
            
        return [types.TextContent(type="text", text=f"搜索结果：\n{res.stdout}")]

    elif name == "read_code_from_wsl":
        READ_CODE_COUNT += 1
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

    elif name == "get_file_structure":
        wsl_path = arguments.get("wsl_file_path")
        try:
            # 读出文件内容
            result = execute_in_target(f"cat {shlex.quote(wsl_path)}")
            if result.returncode != 0:
                return [types.TextContent(type="text", text=f"读取失败: {result.stderr}")]
            
            lines = result.stdout.splitlines()
            structure_info = []
            
            # 正则匹配类名和方法名
            # 匹配类：public class Foo / interface Foo / enum Foo
            class_pattern = re.compile(r'^\s*(?:public|protected|private)?\s*(?:static)?\s*(?:final)?\s*(?:abstract)?\s*(class|interface|enum)\s+(\w+)')
            # 匹配方法：public void foo() / private String bar(int a)
            method_pattern = re.compile(r'^\s*(?:public|protected|private)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?[\w<>\[\]]+\s+(\w+)\s*\(')

            for i, line in enumerate(lines):
                class_match = class_pattern.search(line)
                method_match = method_pattern.search(line)
                
                if class_match:
                    structure_info.append(f"[第 {i+1} 行] {class_match.group(1).capitalize()}: {class_match.group(2)}")
                elif method_match:
                    # 过滤掉 if/for/while 等控制语句，只保留方法定义
                    if method_match.group(1) not in ["if", "for", "while", "switch", "catch"]:
                        structure_info.append(f"[第 {i+1} 行] 方法: {method_match.group(1)}")

            if not structure_info:
                return [types.TextContent(type="text", text="未在文件中找到明显的类或方法结构，请直接使用 read_code_from_wsl 读取全量代码（不传 start_line），自行分析。")]
            
            # 增加强制信任的提示词
            header = f"【权威结构索引】文件 {wsl_path} 的真实结构如下。这是系统底层解析的精准行号，请【完全信任】此结果，直接使用 read_code_from_wsl 读取对应行，不要盲目猜行号或读取全量文件：\n"

            return [types.TextContent(type="text", text=f"文件 {wsl_path} 的结构如下：\n" + "\n".join(structure_info))]
            
        except Exception as e:
            return [types.TextContent(type="text", text=f"结构解析异常: {str(e)}")]

    # ==========================================
    # 下面是合并后的核心工具：记录日志 + 应用补丁
    # ==========================================
    elif name == "patch_and_log":
        # 先初始化
        if WORK_JSON_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_test 初始化当前 Bug 的测试环境。", isError=True)]
        
        round_num = arguments.get("round")
        status = arguments.get("status")
        reasoning = arguments.get("reasoning")
        fix_summary = arguments.get("fix_summary")
        changes = arguments.get("changes", [])

        if not changes:
            return [types.TextContent(type="text", text="参数错误：changes 不能为空（空 patch 会在 work_log 里留下一轮无效记录）。", isError=True)]

        # 第一阶段：全部在内存中校验和替换，order 用来保持落盘顺序。
        staged_writes = {}
        order = []

        # 落盘前先把相对路径补成工作区绝对路径，再做越界校验
        target_paths = [normalize_patch_path(change.get("file_path")) for change in changes]
        blocked = []
        for path in target_paths:
            _ok, _why = check_patch_path(path)
            if not _ok:
                blocked.append(_why)
        if blocked:
            logging.warning(f"[patch] {CURRENT_BUG} 拒绝越界 patch：{blocked}")
            reject_text = (
                "【系统拒绝】patch 路径校验未通过，以下路径不允许修改：\n- "
                + "\n- ".join(blocked)
                + "\n只能修改当前工作区内的文件。本轮修改未落盘。"
            )
            return [types.TextContent(type="text", text=reject_text, isError=True)]

        for idx, change in enumerate(changes):
            wsl_path = target_paths[idx]
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

        # 第二阶段：写入目标文件 + 记账；原子性由 worklog 负责（要么全成，要么全还原）
        writes = [
            (wsl_path, staged_writes[wsl_path]["original"], staged_writes[wsl_path]["content"])
            for wsl_path in order
        ]
        try:
            _worklog().apply(round_num, status, reasoning, fix_summary, writes)
        except worklog.WorkLogError as err:
            msg = f"写入硬盘失败：{err}。已自动还原 {len(err.restored)} 个文件"
            if err.broken:
                msg += f"，但 {len(err.broken)} 个文件还原失败（{', '.join(err.broken)}），请用 read_code_from_wsl 手动检查"
            msg += "。本轮修改未写入工作日志。"
            return [types.TextContent(type="text", text=msg, isError=True)]

        # 更新全局轮次变量，供预算读数使用
        CURRENT_ROUND = round_num
        TOTAL_ATTEMPTS += 1
        READ_CODE_COUNT = 0
        return [types.TextContent(type="text", text="Patch applied successfully. IMMEDIATELY call run_test to verify. Do NOT speak." + budget_line())]

    elif name == "get_work_log":
        # 【多 Bug 隔离】只读当前 Bug 的工作日志
        if WORK_JSON_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_test 初始化当前 Bug 的测试环境。", isError=True)]

        limit = arguments.get("limit", 3)

        if limit <= 0:
            return [types.TextContent(type="text", text="已跳过读取历史日志（limit <= 0）。")]

        if not os.path.exists(WORK_JSON_FILE):
            return [types.TextContent(type="text", text=f"当前 Bug（{CURRENT_BUG}）暂无历史工作日志。")]

        # digest：每轮改了什么、测试怎么变的、还差什么（不回放 old/new 全文）
        try:
            text = _worklog().digest(limit=limit, title=CURRENT_BUG)
        except Exception as err:
            return [types.TextContent(type="text", text=f"读取工作日志失败: {str(err)}", isError=True)]
        return [types.TextContent(type="text", text=text)]

    elif name == "rollback":

        if WORK_JSON_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_test 初始化当前 Bug 的测试环境。", isError=True)]

        target_round = arguments.get("target_round")
        if target_round is None:
            return [types.TextContent(type="text", text="参数错误：必须提供 target_round", isError=True)]

        if not os.path.exists(WORK_JSON_FILE):
            return [types.TextContent(type="text", text=f"当前 Bug（{CURRENT_BUG}）没有历史日志，无需回滚。")]

        restored, broken = _worklog().rollback(target_round)
        if not restored:
            return [types.TextContent(type="text", text=f"在第 {target_round} 轮及之后没有找到可用的备份，未改动任何代码和日志。")]

        msg = (
            f"回滚成功！已撤销当前 Bug（{CURRENT_BUG}）第 {target_round} 轮及之后的修改，"
            f"恢复了 {len(set(restored))} 个文件到修改前的状态。"
        )
        if broken:
            unique_broken = sorted(set(broken))
            msg += f"\n但 {len(unique_broken)} 个文件还原失败（{', '.join(unique_broken)}），请用 read_code_from_wsl 手动检查。"
        msg += "你可以调用 get_work_log 确认当前的历史状态。"
        return [types.TextContent(type="text", text=msg)]

async def main():
    logging.info("🔥 MCP Server 启动！")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
