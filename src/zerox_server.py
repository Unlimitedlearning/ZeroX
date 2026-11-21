import asyncio
import subprocess
import logging
import os
import re
import shlex
import posixpath
import time
from logging.handlers import RotatingFileHandler
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# 项目内模块（解耦第一步：配置与工具 schema 已外迁）
from zerox_core import PASSED_RESULT_MARKER, TASK_FAILED_MARKER
from zerox_core import config as config_mod
from zerox_core import schemas
from zerox_core import resume as resume_mod
# 读工具的工作区边界：纯解析逻辑在 zerox_core.paths，server 只传 CURRENT_WORKDIR
from zerox_core import paths as core_paths
# search_code 的命令构造与结果整理是纯函数，离线可单测
from zerox_core import search as code_search
from zerox_core.config import compile_or_fail
from zerox_core.config import resolve_bug as _resolve_bug
# 命令执行器已外迁：事后审计（audit.py）要用同一条 test_cmd 复跑，不能各写一份 WSL 转发逻辑
from zerox_core.exec_env import IS_WINDOWS, execute_in_target, popen_in_target, win_path_to_wsl
# 外置版本库：work_log 事件流 / blob 快照 / 回滚（独立模块，不依赖本文件任何东西）
import worklog
# 失败诊断：归因卡 / 停滞检测的判定逻辑集中在此，server 只读事件 + 拼返回
import diagnosis
# 判定逻辑只有一份：服务端的「通过前先查有没有动测试」和跑完的事后审计用同一个重放器
import audit

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

# 状态锁与计时器：START_TIME 非 None ⇔ 工作区已检出、且当前任务正在计时。
# 一个变量只描述一件事：setup 失败时就地置回 None
START_TIME = None
CURRENT_ROUND = 0
# 累计补丁数：只用于日志与指标，不是任务终止条件
TOTAL_ATTEMPTS = 0
# 累计测试次数：唯一的强制限制；达到上限即终止任务，不向 agent 报告剩余次数
TOTAL_TESTS = 0

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
# 唯一的强制限制是「测试次数」：达到上限即终止任务。补丁本身不限次。
# 该计数只在服务端用于终止判定，不向 agent 播报，避免把测试暗示成需要节省的稀缺资源。
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
# 【路径约束】只拦「写不进当前工作区」的 patch
# 运行时只管「别写到工作区外面」，patch_allow_globs / patch_deny_globs 默认空，需要时再收紧。
# 动没动测试代码交给事后的 audit.py 判定：Fixed 和 Verified Fixed 分开报。
# ==========================================
def _glob_list(value):
    """把配置里的白名单/黑名单统一成 list[str]；空值 = 不限制。"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value if str(v).strip()]


def check_patch_path(raw_path: str):
    """patch 路径统一入口：归一化 / 白黑名单 / 工作区边界都在 zerox_core.paths。

    server 只注入 CURRENT_WORKDIR 和 target 配置；与读工具共用同一份解析，避免
    「读工具允许工作区内的 ..，patch 却一刀切拒绝」这种两套规则漂移。
    返回 (ok, path_or_reason)，成功时第二个值是归一化后的绝对路径。
    """
    return core_paths.resolve_patch_path(
        raw_path,
        CURRENT_WORKDIR,
        allow=TARGET.get("patch_allow_globs"),
        deny=TARGET.get("patch_deny_globs"),
        must_be_under_workspace=bool(TARGET.get("patch_must_be_under_workspace", True)),
    )


def resolve_read_path(raw_path):
    """读工具的统一入口：纯解析逻辑在 zerox_core.paths，这里只注入当前工作区。"""
    return core_paths.resolve_read_path(raw_path, CURRENT_WORKDIR)


def display_path(wsl_path):
    """回给模型的路径转相对（纯逻辑在 zerox_core.paths，便于离线单测）。"""
    return core_paths.display_path(wsl_path, CURRENT_WORKDIR)


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
    global START_TIME
    if START_TIME is not None:
        logging.info(f"⏱ 任务结束（{status}）：{CURRENT_BUG} 耗时 {round(time.time() - START_TIME, 2)}s，"
                     f"第 {CURRENT_ROUND} 轮，累计 {TOTAL_ATTEMPTS} 次修改")
    
    # 复位计时器和锁状态，准备好下一个 Bug（锁就是 START_TIME：置空即「未就绪」）
    START_TIME = None


def resume_from_worklog(bug_id, workspace, max_tests):
    """【L1 续跑】服务进程重启后，从 work_log 重建这个 bug 的内存状态。

    客户端在续跑的第一轮 run_test 传 resume=true（不传 force_checkout）。本函数
    取代原本的「全量 checkout + 删日志」路径：工作区保持中断时的原样，计数器按
    磁盘事件流恢复，START_TIME 重新计时。不改 work_log 一个字节。

    边界一致性（工作区文件是否与最后一条 patch 匹配）不在服务端自动校验 ——
    续跑是容错兜底而非实验主路径，依赖人先按说明检查工作区，避免把自动校验写成
    一道既慢又可能误拒绝的关卡。

    返回 (ok, message)：work_log 不存在或为空时 ok=False，调用方回退到全新任务。
    """
    global CURRENT_BUG, CURRENT_WORKDIR, WORK_JSON_FILE, TEST_LOG_FILE
    global START_TIME, CURRENT_ROUND, TOTAL_ATTEMPTS, TOTAL_TESTS

    bug_dir = os.path.join(LOG_DIR, bug_id)
    log_file = os.path.join(bug_dir, "work_log.jsonl")
    if not os.path.exists(log_file):
        return False, f"续跑失败：{bug_id} 没有 work_log.jsonl，无法重建进度。请先正常跑一次该 bug。"

    events = worklog.WorkLog(bug_dir).events()
    state = resume_mod.state_from_events(events, max_tests=max_tests)
    if not events:
        return False, f"续跑失败：{bug_id} 的 work_log 没有可解析事件，无法重建进度。"

    CURRENT_BUG = bug_id
    CURRENT_WORKDIR = workspace
    TEST_LOG_FILE = os.path.join(bug_dir, "test.log")
    WORK_JSON_FILE = log_file
    CURRENT_ROUND = state["round"]
    TOTAL_ATTEMPTS = state["attempts"]
    TOTAL_TESTS = state["tests"]
    START_TIME = time.time()  # 续跑段单独计时，不与中断前合并

    msg = (f"【续跑已恢复】{bug_id}：从 work_log 重建 轮次={CURRENT_ROUND}、"
           f"补丁={TOTAL_ATTEMPTS}、测试={TOTAL_TESTS}；工作区保持原样，未重新 checkout。")
    logging.info(msg)
    if state.get("at_limit"):
        msg += (f"\n【注意】历史测试次数已达上限 {max_tests}，本次 run_test 将按上限判定失败。"
                "若确认上次是误中断，请人工核对 work_log 后再决定是否需要归档重跑。")
    return True, msg


# ==========================================
# 【run_test 结果回写】每条结论追加一条 test 事件，永不删除
# 失败数和详情都留在 work_log 里，get_work_log 才能渲染「上轮 5 个 → 2 个」这种对比，
# agent 才知道该微调还是该换方向。
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


def locate_failing_tests(detail_text, workdir, limit=3):
    """本地把失败用例解析成 (类名, 方法, 相对路径, 行号, 方法体)，不耗模型对话轮。

    类名 grep 到测试文件，扫方法定义行，再按花括号配平抽出方法体（断言原文就在里面）；
    任一步失败就跳过对应字段，不把猜测交给模型。
    limit 默认 3：多用例往往是同一根因，列太多只会挤掉真正的报错。
    方法体是「顺手代劳读取」，仅供本次返回，调用方不得写进持久 work_log。
    """
    located = []
    try:
        for cls, method in code_search.parse_failing_tests(detail_text)[:limit]:
            simple = cls.rsplit(".", 1)[-1]
            cmd, err = code_search.build_grep_command(workdir, "*.java", f"class {simple}")
            if err:
                continue
            res = execute_in_target(cmd)
            if res.returncode not in (0, 1) or not res.stdout.strip():
                continue
            path = res.stdout.splitlines()[0].split(":", 2)[0]
            ok, readable = resolve_read_path(path)
            if not ok:
                continue
            src = execute_in_target(f"cat {shlex.quote(readable)}")
            if src.returncode != 0:
                continue
            line_no = code_search.locate_method_line(src.stdout, method)
            rel = posixpath.relpath(path, workdir) if workdir else path
            body = ""
            if line_no:
                body, _lines, _cut = code_search.extract_method_body(
                    src.stdout, line_no, max_lines=50, max_chars=1200
                )
            located.append((cls, method, rel, line_no, body))
    except Exception as err:
        # 本地解析是「省一轮搜索」的优化，任何异常都不能打断 run_test 主返回。
        logging.warning(f"失败用例本地定位跳过: {str(err)}")
    return located


def locate_assertion_details(workspace, max_files=3, chars_per_file=6000):
    """从工作区的测试报告文件里提取 expected/actual，返回可直接拼接的文本。

    Defects4J 的 stdout 只有「Failing tests」用例名清单，断言明细在报告文件里
    （Maven 的 surefire-reports、Ant 的 test-results）。一次 find 找候选 + 逐个截断读取，
    全程本地、不耗模型对话轮。找不到就返回空串——纯增益，不影响原有返回。
    """
    try:
        find_cmd = (
            f"find {shlex.quote(workspace)} "
            r"\( -path '*/surefire-reports/*.txt' -o -path '*/surefire-reports/*.xml' "
            r"-o -path '*/test-results/*.txt' -o -path '*/test-reports/*.txt' \) "
            f"-type f 2>/dev/null | head -n {max_files}"
        )
        found = execute_in_target(find_cmd)
        paths = [p.strip() for p in (found.stdout or "").splitlines() if p.strip()]
        corpus = []
        for path in paths:
            ok, readable = resolve_read_path(path)
            if not ok:
                continue
            res = execute_in_target(f"head -c {chars_per_file} {shlex.quote(readable)}")
            if res.returncode == 0 and res.stdout:
                corpus.append(res.stdout)
        if not corpus:
            return ""
        text, _n = code_search.extract_assertions("\n".join(corpus))
        return text
    except Exception as err:
        # 与失败用例定位同理：报告读取是优化，任何异常都不能打断 run_test。
        logging.warning(f"断言原文本地提取跳过: {str(err)}")
        return ""


# 测试日志安全读取：正常日志几十 KB；超过 2MB 基本是探针打进热路径刷屏或 Java OOM
# 的半成品。整段读进内存会让后续正则/断言提取在数百 MB 文本上卡死（实测 451MB 卡死），
# 因此超限只读头部（编译/启动）+ 尾部（BUILD 结果与 Failing tests），中间明确省略。
LOG_READ_MAX_BYTES = 2 * 1024 * 1024
LOG_HEAD_BYTES = 256 * 1024


def read_test_log(path):
    """安全读取测试日志，返回 (content, truncated)。任何异常都必须由调用方兜底。"""
    size = os.path.getsize(path)
    if size <= LOG_READ_MAX_BYTES:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), False
    tail_bytes = LOG_READ_MAX_BYTES - LOG_HEAD_BYTES
    with open(path, "rb") as f:
        f.seek(0)
        head = f.read(LOG_HEAD_BYTES).decode("utf-8", "replace")
        f.seek(-tail_bytes, os.SEEK_END)
        tail = f.read(tail_bytes).decode("utf-8", "replace")
    return code_search.cap_log_text(head, tail, size), True


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


def patches_touching_tests():
    """当前存活补丁里动过的测试文件（按 target.audit_test_globs）；分不出测试代码时返回 []。

    为什么要在「判定通过」这一刻查：改测试是伪造通过的唯一手法，而一旦通过了，
    客户端就会把这个 pass 当成唯一正样本、metrics 也会记 Fixed
    （Time_11 实测：测试体被换成 assertNotNull(...) 后套件就通过，主代码一个字都没改）。

    判定直接调 audit.audit_events：运行时的检查与事后审计必须是同一套标准，
    否则会出现「跑的时候放过了、审计才报违规」这种自相矛盾的结果（且那份逻辑已被
    tools/selftests/audit_selftest.py 覆盖，不需要在服务端再写一遍）。
    """
    globs = _glob_list(TARGET.get("audit_test_globs"))
    if not globs:
        return []
    wl = _worklog()
    if wl is None:
        return []
    try:
        verdict = audit.audit_events(wl.events(), test_globs=globs)
    except Exception as err:
        logging.error(f"检查补丁是否动过测试失败: {str(err)}")
        return []
    return sorted(v["path"] for v in verdict.get("violations") or [])


server = Server("zerox-server")

@server.list_tools()
async def list_tools():
    # schema 是纯数据，已外迁到 src/zerox_core/schemas.py
    return schemas.build_tools()

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    global current_log_handle, current_process, WORK_JSON_FILE, TEST_LOG_FILE, CURRENT_BUG
    global START_TIME, CURRENT_ROUND, TOTAL_ATTEMPTS, TOTAL_TESTS
    global CURRENT_WORKDIR

    # 🚨 绝杀：每次调用任何工具前，强制等待上一次的测试进程跑完（防并发冲突）
    wait_for_previous_process()

    if name == "run_test":
        bug_id = arguments.get("bug_id", "Lang_1")
        force_checkout = arguments.get("force_checkout", False)
        # 【L1 续跑】客户端首轮显式传 resume=true：不 checkout、不删日志，按 work_log 恢复进度。
        resume = bool(arguments.get("resume", False))

        # 校验格式：解析规则来自 config.json 的 target.bug_id_pattern
        bug_ctx = resolve_bug(bug_id)
        if bug_ctx is None:
            return [types.TextContent(type="text", text=f"bug_id 格式错误，应符合 config.json 中 target.bug_id_pattern 的规则：{TARGET['bug_id_pattern']}", isError=True)]

        new_bug_id = bug_ctx["bug_id"]
        workspace = bug_ctx["workspace"]
        setup_cmd = bug_ctx["setup_cmd"]
        test_cmd = bug_ctx["test_cmd"]

        # 【续跑优先】恢复到「已有进度」的状态后再走下面的通用判定：此时 START_TIME 非 None、
        # TOTAL_ATTEMPTS/TOTAL_TESTS 为磁盘重建值，自然跳过重新 checkout，也不会被次数上限误判为新任务。
        if resume:
            ok, msg = resume_from_worklog(new_bug_id, workspace, MAX_TESTS)
            if not ok:
                return [types.TextContent(type="text", text=msg, isError=True)]

        # 【测试次数上限】测试次数达到上限即终止任务 —— 这是服务端唯一的强制限制。
        # 放在 checkout 之前有两个理由：
        #   1. 次数已经用尽时，没必要再花时间准备一个不会跑测试的工作区；
        #   2. 只有「换了 bug」才算新任务并复位 TOTAL_TESTS。否则 agent 自己传一个
        #      force_checkout=true 就能把次数重置成满额，这道限制即可被绕过。
        is_new_bug = CURRENT_BUG != new_bug_id
        if not is_new_bug and TOTAL_TESTS >= MAX_TESTS:
            reset_task_state("MaxTests")
            return [types.TextContent(type="text", text=(
                f"{TASK_FAILED_MARKER}已达到最大测试次数（{MAX_TESTS} 次）。请立刻停止修复并输出 [FAILED]。\n"
                f"本任务共提交 {TOTAL_ATTEMPTS} 个补丁。"
            ), isError=True)]
        
        # ---- 是否需要重新 checkout ----
        # 唯一的原则：run_test 只在「明确要求」或「确实是新任务」时重置工作区，
        # 绝不在任务中途悄悄 checkout —— 全量 checkout 要 2-3 分钟，还会把已打好的补丁打回 buggy。
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
            elif START_TIME is None and TOTAL_ATTEMPTS == 0:
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
            # 留着只会误导 agent（和指标里的 current_round），直接删除
            if os.path.exists(WORK_JSON_FILE):
                try:
                    os.remove(WORK_JSON_FILE)
                    logging.info(f"已清空 {CURRENT_BUG} 的历史 work_log（工作区被重新检出）")
                except OSError as err:
                    logging.error(f"清空 work_log 失败: {str(err)}")
            
            # 重置锁和计时器（锁已并入 START_TIME：非 None = 工作区已检出且有计时在跑）
            START_TIME = time.time()
            CURRENT_ROUND = 0
            TOTAL_ATTEMPTS = 0
            # 只有新 bug 才复位测试计数：同一个 bug 上的 force_checkout 不能当「续命」用
            if is_new_bug:
                TOTAL_TESTS = 0
            
            logging.info(f"切换当前 Bug 为 {CURRENT_BUG} | 执行全量 Checkout")

            # setup_cmd 与 test_cmd 仍然分两步跑：准备阶段失败时，不能拿半成品工作区去跑增量测试。
            # setup_cmd 为空（工作区已存在、无需重置）时直接跳过准备阶段。
            if setup_cmd:
                try:
                    setup_res = execute_in_target(setup_cmd, timeout=TEST_TIMEOUT)
                except subprocess.TimeoutExpired:
                    msg = f"【环境准备超时】setup_cmd 超过 {TEST_TIMEOUT}s 未结束，已放弃本轮。"
                    # 超时原因必须同时进 mcp_server.log 和 work_log，否则事后无法复现
                    logging.error(f"【环境准备超时】{CURRENT_BUG} setup_cmd 超过 {TEST_TIMEOUT}s 未结束")
                    save_test_result("SetupFailed", None, msg)
                    # 工作区可能是半成品：标回未初始化，下次 run_test 重新 checkout
                    START_TIME = None
                    return [types.TextContent(type="text", text=msg, isError=True)]
                if setup_res.returncode != 0:
                    setup_out = ((setup_res.stdout or "") + (setup_res.stderr or "")).strip()
                    msg = f"【环境准备失败】setup_cmd 退出码 {setup_res.returncode}：\n{setup_out[-2000:]}"
                    # 同一条道理：失败原文必须进日志，否则 mcp_server.log 里只剩「执行全量 Checkout」的空尾
                    logging.error(f"【环境准备失败】{CURRENT_BUG} setup_cmd 退出码 {setup_res.returncode}：\n{setup_out[-2000:]}")
                    save_test_result("SetupFailed", None, msg)
                    # 同上：别拿一个损坏的工作区继续跑增量测试
                    START_TIME = None
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
                # （START_TIME 兼做就绪标志：重新起算即可）
                START_TIME = time.time()
                return [types.TextContent(type="text", text=f"【测试超时】执行超过 {TEST_TIMEOUT} 秒仍未结束，进程已被强制终止。", isError=True)]

            logging.info("测试进程结束，正在解析日志...")
            
            # 读取并解析日志
            if not os.path.exists(TEST_LOG_FILE):
                save_test_result("Unknown", None, "测试完成，但服务端未生成测试日志。请重试 run_test；若连续出现，停止并说明情况。")
                return [types.TextContent(type="text", text="测试完成，但服务端未生成测试日志。请重试 run_test；若连续出现，停止并说明情况。", isError=True)]
            
            content, log_truncated = read_test_log(TEST_LOG_FILE)
            truncated_notice = ""
            if log_truncated:
                truncated_notice = (
                    "\n\n【日志异常庞大（已截断）】测试日志超过 2MB，中间重复输出已省略"
                    "（只保留开头与末尾）。这通常是调试输出被放进了高频调用路径，或 Java 进程 OOM。"
                    "请检查最近补丁是否把打印语句/高开销调用放进了循环或高频方法；"
                    "断言原文已改为从测试报告文件本地提取。"
                )

            if not content.strip():
                save_test_result("EmptyLog", None, "【日志为空】测试没有任何输出，无法判定结果。请重试一次 run_test。")
                return [types.TextContent(type="text", text="【日志为空】测试没有任何输出，无法判定结果。请重试一次 run_test。")]

            exit_code = current_process.returncode

            # 【可配置判定】通过/失败规则来自 config.json 的 target.passed_pattern / failed_pattern
            if PASS_RE.search(content):
                # 【假通过检查】通过的前置条件：这次跑的是原始测试。改测试能造出通过，但造不出修复，
                # 所以只把「原始测试上的通过」当成正样本；被污染的那次会明确要求它先 rollback。
                dirty = patches_touching_tests()
                if dirty:
                    names = "、".join(p.rsplit("/", 1)[-1] for p in dirty)
                    msg = (
                        f"【测试通过但不算数】这次通过是在改过的测试文件上跑出来的：{names}。\n"
                        "改测试能造出通过，但改不出修复。请 rollback 撤销改测试的那几轮，再 run_test："
                        "在原始测试上通过才算修复。"
                    )
                    save_test_result("TaintedPass", 0, msg)
                    return [types.TextContent(type="text", text=msg)]

                save_test_result("Passed", 0, f"{PASSED_RESULT_MARKER}命中 passed_pattern")
                reset_task_state("Fixed")
                return [types.TextContent(type="text", text=f"{PASSED_RESULT_MARKER}该 Bug 已成功修复！请结束任务并输出 [FIXED]。")]

            fail_match = FAIL_RE.search(content)
            if exit_code not in (None, 0) and fail_match is None:
                # 【本地代劳】先给结构化的「文件:行号: 错误」清单，模型不必从几百行 javac
                # 输出里自己找错误行；提取不到时回退原文尾部，保证任何格式都有可操作信息。
                errors_text, err_count = code_search.extract_compile_errors(content)
                compile_msg = f"【编译失败】退出码 {exit_code}。"
                if err_count:
                    compile_msg += f"\n{errors_text}\n\n以下为编译日志尾部（原文）：\n{content[-1200:]}"
                else:
                    compile_msg += f"\n请根据日志修复错误：\n{content[-2500:]}"
                compile_msg += truncated_notice
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
                # fail_msg 是持久化的 detail（进 work_log → brief 的「最近报错」取末行），
                # 因此只放稳定的失败事实；stderr 尾部是一次性调试信息，只随本次返回给模型。
                fail_msg = f"【测试失败】共 {count_text} 个用例失败。\n以下是报错信息：\n{combined}"

                # 【本地代劳】失败用例的源文件/方法行/方法体由服务端解析，模型不必再发
                # search_code + get_file_internal_structure + read_specific_code 三轮工具。
                # 失败信息里本来就有类名，grep 一次 + 扫方法定义行 + 配平抽方法体，只读、不耗测试次数。
                located = locate_failing_tests(failing_info, workspace)
                bodies = []
                if located:
                    loc_lines = [f"- {cls}::{method} → {rel}" + (f":{no}" if no else "")
                                 for cls, method, rel, no, _body in located]
                    fail_msg += ("\n\n【失败用例位置（本地解析，行号可直接用 read_specific_code 读）】\n"
                                 + "\n".join(loc_lines))
                    # 方法体只进一次性 surface，不写进持久 detail：
                    # worklog.brief() 取 detail 末行当「最近报错」，塞入源码会污染压缩后的状态卡。
                    bodies = [(f"{cls}::{method}", body)
                              for cls, method, _rel, _no, body in located if body]
                fail_msg += "\n\n请分析并修复。"
                save_test_result("Failed", fail_count, fail_msg)

                # 【stderr 一并给出】测试进程的 stderr 自启动起就合并进 TEST_LOG_FILE（STDOUT），
                # 旧版只回 Failing tests 段，把探针写进 System.err 的输出整段丢掉，逼模型改用
                # 「探针写文件再读回」。这里补一段失败段之前的日志尾，含 stderr 与探针输出。
                surface = fail_msg
                # 【失败用例方法体（断言原文）】直接附上，省去模型自己定位再读文件。
                if bodies:
                    surface += "\n\n【失败用例方法体（本地代劳读取，断言原文见此）】\n"
                    surface += "\n\n".join(f"--- {name} ---\n{body}" for name, body in bodies)
                fail_pos = content.find(fail_match.group(0))
                prefix = content[max(0, fail_pos - 1200):fail_pos].strip() if fail_pos > 0 else ""
                if prefix:
                    surface += "\n\n【测试日志尾部（含 stderr）】\n" + prefix[-1200:]

                # 【断言原文】先从已读入的 test.log 提取；stdout 没有堆栈时再查工作区报告文件
                # （surefire-reports / test-results）。两者都提取不到才退回取法指引。
                assertion_text, _n = code_search.extract_assertions(content)
                if not assertion_text:
                    assertion_text = locate_assertion_details(workspace)
                if assertion_text:
                    surface += "\n\n" + assertion_text
                else:
                    # 固定取法说明已移入 AGENTS.md（system prompt，前缀缓存只付一次），
                    # 这里只留一句极简指引。
                    surface += "\n\n[提示] 断言原文（expected/actual）的取法见系统提示。"

                # 【归因卡 + 停滞提示】读当前 work_log 事件流本地生成，省去模型翻日志。
                # 一次性 surface，不写进持久 detail（brief 取 detail 末行会被污染）。
                try:
                    wl = _worklog()
                    events = wl.events() if wl is not None else []
                    card = diagnosis.build_failure_card(events)
                    if card:
                        surface += "\n\n" + card
                    stall = diagnosis.detect_stall(events)
                    if stall:
                        surface += "\n\n" + stall
                except Exception as err:
                    logging.warning(f"失败归因卡生成跳过: {str(err)}")

                surface += truncated_notice
                return [types.TextContent(type="text", text=surface)]

            tail_msg = f"最新日志如下（退出码 {exit_code}）:\n{content[-2000:]}"
            tail_msg += truncated_notice
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
        pattern = arguments.get("file_pattern") or TARGET["source_glob"]
        regex = bool(arguments.get("regex", False))
        max_results = arguments.get("max_results", code_search.DEFAULT_MAX_RESULTS)

        # 工作区统一用 resolve_bug 解析出来的 CURRENT_WORKDIR，不按测试工具命名推导
        workdir = CURRENT_WORKDIR
        if not workdir:
            return [types.TextContent(type="text", text="【系统拒绝】还没解析出当前工作区，请先调用 run_test 初始化。", isError=True)]
        
        # grep 的 --include 只匹配 basename。模型常传 "src/main/java/.../Foo.java" 或
        # "*tz/*.java" 这种带路径的模式，直接拼进 --include 会静默返回「未找到」，
        # 模型还会反过来怀疑是自己用错了工具（Time_11 的 turn 12/13 各出现一次）。
        # 现在把路径拆开：basename 给 --include，不带通配的路径前缀当搜索目录；
        # 前缀下没有命中就退回整个工作区，并在结果里说明换了范围。
        include = pattern.rsplit("/", 1)[-1] or TARGET["source_glob"]
        prefix = pattern.rsplit("/", 1)[0] if "/" in pattern else ""
        scope = workdir
        if prefix and "*" not in prefix:
            scope = posixpath.normpath(
                prefix if prefix.startswith("/") else posixpath.join(workdir, prefix)
            )

        # 【只读边界】file_pattern 里带绝对路径时（如 pristine/ 里的基线），prefix 会被
        # 直接当搜索目录用 —— 之前这是绕过工作区隔离、读到基线的第二个入口。
        # 解析成工作区内绝对路径后再搜；越界就明确拒绝，不静默退化。
        ok_scope, scope_or_err = resolve_read_path(scope)
        if not ok_scope:
            return [types.TextContent(type="text", text=scope_or_err, isError=True)]
        scope = scope_or_err

        cmd, arg_err = code_search.build_grep_command(scope, include, keyword, regex=regex)
        if arg_err:
            return [types.TextContent(type="text", text=arg_err, isError=True)]
        res = execute_in_target(cmd)

        # grep: 0=有命中, 1=无命中, 2=命令出错。旧实现把 stderr 丢掉且把 2 当 1，
        # 搜 'public int[] add' 时 grep 报未闭合字符类，却回「未找到」，模型连搜两次。
        # 但 exit 2 也可能只是个别子目录不可读而 stdout 已有命中，此时不能丢结果。
        def grep_failed(result):
            return result.returncode not in (0, 1)

        if grep_failed(res) and not res.stdout.strip():
            detail = (res.stderr or res.stdout or "").strip()[-500:]
            return [types.TextContent(type="text", text=(
                f"【搜索失败】命令执行出错（退出码 {res.returncode}）：{detail}。"
                "若要按正则搜索，请传 regex=true；否则关键字按字面处理。"
            ), isError=True)]

        note = ""
        if grep_failed(res):
            note = "\n（部分目录读取失败，以上为已扫到的结果）"
        elif res.returncode == 1 and scope != workdir:
            res = execute_in_target(
                code_search.build_grep_command(workdir, include, keyword, regex=regex)[0]
            )
            if grep_failed(res) and not res.stdout.strip():
                detail = (res.stderr or res.stdout or "").strip()[-500:]
                return [types.TextContent(type="text", text=(
                    f"【搜索失败】命令执行出错（退出码 {res.returncode}）：{detail}",
                ), isError=True)]
            note = (f"\n（{scope} 下没有命中，已改为在整个工作区搜索）"
                    if not grep_failed(res) else
                    "\n（部分目录读取失败，以上为已扫到的结果；已改为在整个工作区搜索）")

        if res.returncode == 1 or not res.stdout.strip():
            return [types.TextContent(type="text", text=(
                f"未找到关键字 '{keyword}'（过滤条件：--include={include}，"
                f"{'正则' if regex else '字面'}匹配）。"
                "请换更短的词，或确认 file_pattern 是否写对（只匹配文件名，如 *.java）；"
                "也可直接用 read_specific_code 读取文件。"
            ))]

        body = code_search.format_results(
            res.stdout, keyword, workdir, include, max_results=max_results, regex=regex
        )
        return [types.TextContent(type="text", text=body + note)]

    elif name == "read_specific_code":
        ok_path, path_or_err = resolve_read_path(arguments.get("wsl_file_path"))
        if not ok_path:
            return [types.TextContent(type="text", text=path_or_err, isError=True)]
        wsl_path = path_or_err
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

            # 行号边界：越界时明确说明总行数，避免返回空结果让模型原样重试
            if start_line < 1 or end_line < start_line:
                return [types.TextContent(type="text", text=(
                    f"行号无效：start_line={start_line}, end_line={end_line}。"
                    f"{display_path(wsl_path)} 共 {total_lines} 行，请传 1 ≤ start_line ≤ end_line ≤ {total_lines}。"
                ), isError=True)]

            if start_line > total_lines:
                return [types.TextContent(type="text", text=(
                    f"起始行超出文件末尾：{display_path(wsl_path)} 共 {total_lines} 行，start_line={start_line}。"
                    f"请改读 1-{total_lines} 之间的范围，或改用 search_code 定位。"
                ), isError=True)]

            start_idx = start_line - 1
            end_idx = min(total_lines, end_line)
            selected_lines = lines[start_idx:end_idx]

            # 带行号输出
            formatted_lines = [f"{start_idx + i + 1}: {line}" for i, line in enumerate(selected_lines)]

            header = f"文件 {display_path(wsl_path)} 第 {start_idx + 1}-{end_idx} 行（共 {total_lines} 行）内容如下："
            if end_line > total_lines:
                header += f"\n（end_line={end_line} 超出末尾，已截断到 {total_lines}）"
            return [types.TextContent(type="text", text=header + "\n" + "\n".join(formatted_lines))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"异常: {str(e)}")]

    elif name == "get_file_internal_structure":
        ok_path, path_or_err = resolve_read_path(arguments.get("wsl_file_path"))
        if not ok_path:
            return [types.TextContent(type="text", text=path_or_err, isError=True)]
        wsl_path = path_or_err
        try:
            # 读出文件内容
            result = execute_in_target(f"cat {shlex.quote(wsl_path)}")
            if result.returncode != 0:
                return [types.TextContent(type="text", text=f"读取失败: {result.stderr}")]
            
            items, truncated = code_search.extract_java_structure(result.stdout)
            if not items:
                return [types.TextContent(type="text", text="未在文件中找到明显的类或方法结构。可先用 read_specific_code 读取前 80 行，或用 search_code 定位具体方法名后再读该范围。")]

            kind_names = {"class": "类", "interface": "接口", "enum": "枚举", "record": "记录",
                          "method": "方法", "constructor": "构造函数"}
            structure_info = []
            for it in items:
                lo, hi = it["line_start"], it["line_end"]
                span = str(lo) if lo == hi else f"{lo}-{hi}"
                kind = kind_names.get(it["kind"], it["kind"])
                structure_info.append(f"[第 {span} 行] {kind}: {it['signature']}")
            if truncated:
                structure_info.append("…（条目过多已截断，请用 search_code 缩小范围）")

            # 输出签名（含参数类型）+ 起止行：重载可区分，模型一次 read 就能覆盖完整方法。
            # 措辞仍不承诺绝对权威——源码扫描会漏匿名类等边缘结构，最终以实际代码为准。
            header = (
                f"【结构索引】{display_path(wsl_path)} 的类/方法签名与行范围（由源码扫描得到，"
                "含包级私有方法与构造函数；行号可作定位起点，最终以实际代码为准）：\n"
                "请用 read_specific_code 按 start_line/end_line 只读取需要的那几段，"
                "不要读取全文件；同名方法用参数类型区分，起止行可直接作为锚点补丁的范围。\n"
            )

            return [types.TextContent(type="text", text=header + "\n".join(structure_info))]
            
        except Exception as e:
            return [types.TextContent(type="text", text=f"结构解析异常: {str(e)}")]

    # ==========================================
    # 下面是合并后的核心工具：记录日志 + 应用补丁
    # ==========================================
    elif name == "patch_and_log":
        # 先初始化
        if WORK_JSON_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_test 初始化当前 Bug 的测试环境。", isError=True)]
        
        # 轮号由服务端自增，不采信模型传入的值：模型常在各轮里重复填同一个数，
        # 而 rollback 的「撤销第 N 轮及之后」完全依赖这个编号。
        round_num = (CURRENT_ROUND or 0) + 1
        reasoning = arguments.get("reasoning")
        fix_summary = arguments.get("fix_summary")
        changes = arguments.get("changes", [])

        if not changes:
            return [types.TextContent(type="text", text="参数错误：changes 不能为空（空 patch 会在 work_log 里留下一轮无效记录）。", isError=True)]

        # 第一阶段：全部在内存中校验和替换，order 用来保持落盘顺序。
        staged_writes = {}
        order = []

        # 落盘前统一校验：check_patch_path 顺带把相对路径补成工作区内绝对路径
        checked = [check_patch_path(change.get("file_path")) for change in changes]
        blocked = [why for ok, why in checked if not ok]
        if blocked:
            logging.warning(f"[patch] {CURRENT_BUG} 拒绝越界 patch：{blocked}")
            reject_text = (
                "【系统拒绝】patch 路径校验未通过，以下路径不允许修改：\n- "
                + "\n- ".join(blocked)
                + "\n只能修改当前工作区内的文件。本轮修改未落盘。"
            )
            return [types.TextContent(type="text", text=reject_text, isError=True)]

        target_paths = [path for _ok, path in checked]

        # 按文件分组：一个文件只回读一次。同一文件的多个 change 必须一次性交给
        # apply_line_patches（它对同一份原始行数组校验锚点、倒序应用），不能逐个叠加。
        grouped = {}  # wsl_path -> [patch_diff]
        for idx, change in enumerate(changes):
            wsl_path = target_paths[idx]
            if not wsl_path:
                return [types.TextContent(type="text", text="参数错误：file_path 不能为空。", isError=True)]
            pd = change.get("patch_diff", {})
            if not isinstance(pd, dict):
                return [types.TextContent(type="text", text="参数错误：patch_diff 必须是对象。", isError=True)]
            grouped.setdefault(wsl_path, []).append(pd)
            if wsl_path not in staged_writes:
                staged_writes[wsl_path] = None
                order.append(wsl_path)

        for wsl_path, pds in grouped.items():
            try:
                res = execute_in_target(f"cat {shlex.quote(wsl_path)}")
                if res.returncode != 0:
                    return [types.TextContent(type="text", text=f"修改失败：找不到文件 {display_path(wsl_path)}", isError=True)]
                content = res.stdout.replace("\r\n", "\n")  # 顺手解决 CRLF 问题
                original = res.stdout  # 留给备份和失败还原
            except Exception as e:
                return [types.TextContent(type="text", text=f"读取文件失败: {str(e)}", isError=True)]

            # 行号锚点：一次处理该文件的所有 change，对原始内容校验锚点后倒序替换。
            after, err = code_search.apply_line_patches(content, pds)
            if err:
                return [types.TextContent(type="text", text=f"补丁未应用：{display_path(wsl_path)} —— {err}。本轮修改未落盘。", isError=True)]

            staged_writes[wsl_path] = {"content": after, "original": original}

        # 第二阶段：写入目标文件 + 记账；原子性由 worklog 负责（要么全成，要么全还原）
        writes = [
            (wsl_path, staged_writes[wsl_path]["original"], staged_writes[wsl_path]["content"])
            for wsl_path in order
        ]
        try:
            _worklog().apply(round_num, "PATCHED", reasoning, fix_summary, writes)
        except worklog.WorkLogError as err:
            msg = f"写入硬盘失败：{err}。已自动还原 {len(err.restored)} 个文件"
            if err.broken:
                msg += f"，但 {len(err.broken)} 个文件还原失败（{', '.join(err.broken)}），请用 read_specific_code 手动检查"
            msg += "。本轮修改未写入工作日志。"
            return [types.TextContent(type="text", text=msg, isError=True)]

        # 【热路径提示】新增打印若落在 sample/parse/循环类方法，提前一句客观位置提示，
        # 拦住「探针打进热路径刷屏日志拖垮 JVM」。只陈述代码事实，不判断意图。
        # 一次性返回，不写进 work_log（避免污染 brief 的「最近报错」末行）。
        hotspot_notice = ""
        try:
            for hp_path, hp_pds in grouped.items():
                hp_source = staged_writes[hp_path]["original"]
                hp_changes = [(pd.get("start_line"), pd.get("end_line"), pd.get("new"))
                              for pd in hp_pds]
                notice = diagnosis.detect_hotspot(hp_source, hp_changes)
                if notice:
                    hotspot_notice += "\n\n" + notice
        except Exception as err:
            logging.warning(f"热路径提示生成跳过: {str(err)}")

        # 更新全局轮次变量，供失败信息与指标使用
        CURRENT_ROUND = round_num
        TOTAL_ATTEMPTS += 1
        return [types.TextContent(type="text", text="补丁已应用。请立刻调用 run_test 验证，不要输出解释。" + hotspot_notice)]

    elif name == "get_work_log":
        # 【多 Bug 隔离】只读当前 Bug 的工作日志
        if WORK_JSON_FILE is None:
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_test 初始化当前 Bug 的测试环境。", isError=True)]

        limit = arguments.get("limit", 3)

        if limit <= 0:
            return [types.TextContent(type="text", text="已跳过读取历史日志（limit <= 0）。")]

        if not os.path.exists(WORK_JSON_FILE):
            return [types.TextContent(type="text", text=f"当前 Bug（{CURRENT_BUG}）暂无历史工作日志。")]

        # digest：每轮改了什么、测试怎么变的、还差什么（不回放完整补丁内容）
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
        reasoning = (arguments.get("reasoning") or "").strip()
        kind = arguments.get("kind") or "deadend"

        if not os.path.exists(WORK_JSON_FILE):
            return [types.TextContent(type="text", text=f"当前 Bug（{CURRENT_BUG}）没有历史日志，无需回滚。")]

        restored, broken = _worklog().rollback(target_round, reasoning=reasoning, kind=kind)
        if not restored:
            return [types.TextContent(type="text", text=f"在第 {target_round} 轮及之后没有找到可用的备份，未改动任何代码和日志。")]

        # 撤销之后轮次必须退回去：否则下一轮的 patch/test 会挂在已被撤销的轮号上，
        # 下次回滚的 >= target 范围、digest 的「上轮 N → 好转」和 metrics 的 current_round 全会虚高。
        try:
            CURRENT_ROUND = max(0, int(target_round) - 1)
        except (TypeError, ValueError):
            pass

        msg = (
            f"回滚成功！已撤销当前 Bug（{CURRENT_BUG}）第 {target_round} 轮及之后的修改，"
            f"恢复了 {len(set(restored))} 个文件到修改前的状态。"
        )
        if reasoning:
            msg += f"\n回滚理由已写入工作日志（kind={kind}）：{reasoning}"
        else:
            msg += "\n下次调用 rollback 请传 reasoning，把「探针无效/方向失败」的结论留在日志里，否则被撤销的轮次会连同它的 reasoning 一起进归档，你就再也看不到了。"
        if broken:
            unique_broken = sorted(set(broken))
            msg += f"\n但 {len(unique_broken)} 个文件还原失败（{', '.join(unique_broken)}），请用 read_specific_code 手动检查。"
        msg += "你可以调用 get_work_log 确认当前的历史状态。"
        return [types.TextContent(type="text", text=msg)]

async def main():
    logging.info("🔥 MCP Server 启动！")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
