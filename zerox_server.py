import asyncio
import subprocess
import sys
import logging
import os
import json
import re
import shlex
import fnmatch
import posixpath
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
# 当前 bug 的工作区（由 config.json 的 target.workspace_template 解析而来）
CURRENT_WORKDIR = None

# 新增：状态锁与计时器变量
IS_TASK_INITIALIZED = False
START_TIME = None
CURRENT_ROUND = 0
TOTAL_ATTEMPTS = 0
READ_CODE_COUNT = 0 
CODE_MODIFIED = False
# 【防作弊】测试文件哈希基线：{受保护文件路径: md5}；None = 还没建立
TEST_GUARD_BASELINE = None
# 基线为空（globs 没匹配到任何文件）：视为配置错误，直接拒绝跑测试
TEST_GUARD_EMPTY = False
# 防作弊违规累计次数，到上限直接熔断
GUARD_VIOLATIONS = 0
GUARD_VIOLATION_LIMIT = 3
# 本次任务是否已经修复完成（用于拦住修复后的重复 run_test，避免二次 checkout 把补丁冲掉）
TASK_FINISHED = False

# ==========================================
# [跨平台] 运行环境检测
# ==========================================
IS_WINDOWS = sys.platform == "win32"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
BACKUP_ROOT = os.path.join(BASE_DIR, "backups")
LOG_FILE = os.path.join(LOG_DIR, "mcp_server.log")
# 配置文件：run_benchmark.py 会通过 ZEROX_CONFIG 传进来，保证客户端/服务端读同一份
CONFIG_FILE = os.getenv("ZEROX_CONFIG") or os.path.join(BASE_DIR, "config.json")

# 基准测试指标（logs/benchmark_metrics.jsonl）由 run_benchmark.py 负责写入：
# token 用量只有调用 LLM 的客户端才知道，服务端这一侧只留耗时日志

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
# 目标环境默认配置：换测试集只改 config.json，代码不用动
# 下面这些默认值与原 Defects4J 行为等价；config.json 里 target / agent 段落的同名字段会覆盖它们。
# 可用占位符：{bug_id} {project} {number} {version} {suffix} {tool} {workspace_root} {workspace}
# ==========================================
DEFAULT_TARGET = {
    # 测试框架/构建工具入口，命令模版里的 {tool}
    "tool_path": "",
    # 所有 bug 工作区的根目录，模版里的 {workspace_root}
    "workspace_root": "",
    # 单个 bug 的工作区目录模版，模版里的 {workspace}
    "workspace_template": "{workspace_root}/d4j_{project}_{version}",
    # 首次进入某个 bug 时的准备命令；留空 = 工作区已存在、无需重置
    "setup_cmd": "rm -rf {workspace} && {tool} checkout -p {project} -v {version} -w {workspace}",
    # 每次要验证时执行的测试命令
    "test_cmd": "{tool} test -w {workspace}",
    # 增量测试前是否要求工作区目录已存在
    "require_workspace": True,
    # search_code 的默认文件通配符
    "source_glob": "*.java",
    # bug_id 解析规则：命名分组会自动成为命令/目录模版里的占位符
    "bug_id_pattern": r"^(?P<project>[A-Za-z]+)_(?P<number>\d+)(?P<suffix>[bf]?)$",
    # 规范 bug 名（日志/备份目录/work_log 的 bug 字段用它）
    "bug_id_template": "{project}_{version}",
    # 解析出 number 但没有 suffix 时补的默认后缀
    "default_version_suffix": "b",
    # 判定「全部通过」的规则
    "passed_pattern": r"Failing tests:\s*0",
    # 判定「测试失败」的规则：count = 失败数，detail = 详细堆栈（两个命名分组都可以省略）
    "failed_pattern": r"Failing tests:\s*(?P<count>[1-9]\d*)(?P<detail>[\s\S]*)",
    # ---- 防作弊 P0-1：patch 路径白名单（fnmatch 语法，空 = 不限制）----
    "patch_allow_globs": ["*/src/main/java/*", "*/src/java/*", "*/source/*"],
    # 测试代码/受保护文件：命中即拒绝，优先级高于白名单
    "patch_deny_globs": ["*/src/test/*", "*/tests/*", "*/test/*"],
    # patch 是否必须落在当前工作区内
    "patch_must_be_under_workspace": True,
    # ---- 防作弊 P0-2：测试文件哈希校验（checkout 后拍基线，跑测试前比对）----
    "test_guard_enabled": True,
    "test_guard_globs": ["*/src/test/*", "*/tests/*", "*/test/*"],
    # 被改动时是否自动 git checkout / rm 还原（还原后本轮测试仍然被拒绝）
    "test_guard_autorestore": True,
}

DEFAULT_AGENT = {
    # 单个 bug 允许的 patch_and_log 次数上限
    "max_attempts": 10,
}


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

    for key in ["target", "timeouts"]:
        if key not in config:
            logging.error(f"❌ Fatal: Missing '{key}' in '{CONFIG_FILE}'.")
            sys.exit(1)

    max_attempts = (config.get("agent") or {}).get("max_attempts", DEFAULT_AGENT["max_attempts"])
    if not isinstance(max_attempts, int) or max_attempts <= 0:
        logging.error(f"❌ Fatal: 'max_attempts' must be a positive integer in '{CONFIG_FILE}'.")
        sys.exit(1)
        
    if not isinstance(config["timeouts"].get("test_execution"), int):
        logging.error(f"❌ Fatal: 'timeouts.test_execution' must be an integer in '{CONFIG_FILE}'.")
        sys.exit(1)

    return config

def load_target_config(config):
    """把 config.json 的 target 段落合并到默认值上；缺字段就用默认值。"""
    target = dict(DEFAULT_TARGET)
    user_target = config.get("target") or {}
    if not isinstance(user_target, dict):
        logging.error(f"❌ Fatal: 'target' must be an object in '{CONFIG_FILE}'.")
        sys.exit(1)
    for key, value in user_target.items():
        if value is not None:
            target[key] = value
    return target


def load_agent_config(config):
    """agent 段落（行为开关）单独合并；缺字段就用默认值。"""
    agent_cfg = dict(DEFAULT_AGENT)
    user_agent = config.get("agent") or {}
    if not isinstance(user_agent, dict):
        logging.error(f"❌ Fatal: 'agent' must be an object in '{CONFIG_FILE}'.")
        sys.exit(1)
    for key, value in user_agent.items():
        if value is not None:
            agent_cfg[key] = value
    return agent_cfg


def compile_or_fail(pattern: str, name: str):
    """正则写错就 Fail Fast，别等跑到一半才报错。"""
    try:
        return re.compile(pattern)
    except re.error as err:
        logging.error(f"❌ Fatal: '{name}' is not a valid regex ({pattern}): {err}")
        sys.exit(1)


CONFIG = load_config()
TARGET = load_target_config(CONFIG)
AGENT_CONFIG = load_agent_config(CONFIG)
TOOL_PATH = TARGET["tool_path"]
WORKSPACE_ROOT = (TARGET["workspace_root"] or "").rstrip("/")
MAX_ATTEMPTS = AGENT_CONFIG["max_attempts"]
TEST_TIMEOUT = CONFIG["timeouts"]["test_execution"]

# 判定规则与 bug_id 规则全部来自 config.json（换测试集不用改代码）
BUG_ID_RE = compile_or_fail(TARGET["bug_id_pattern"], "target.bug_id_pattern")
PASS_RE = compile_or_fail(TARGET["passed_pattern"], "target.passed_pattern")
FAIL_RE = compile_or_fail(TARGET["failed_pattern"], "target.failed_pattern")

if not str(TARGET["test_cmd"] or "").strip():
    logging.error(f"❌ Fatal: 'target.test_cmd' cannot be empty in '{CONFIG_FILE}'.")
    sys.exit(1)

if "{tool}" in f"{TARGET['setup_cmd']} {TARGET['test_cmd']}" and not TOOL_PATH:
    logging.error(f"❌ Fatal: 'target.tool_path' is required when commands use {{tool}} in '{CONFIG_FILE}'.")
    sys.exit(1)

logging.info(
    f"运行环境：{'Windows(WSL 转发)' if IS_WINDOWS else 'Linux(原生)'} | "
    f"tool: {TOOL_PATH or '(未配置)'} | workspace_root: {WORKSPACE_ROOT}"
)


def safe_format(template: str, values: dict) -> str:
    """模版渲染：找不到的占位符原样留着，别让少一个键就把工具搞崩。"""
    class _SafeDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    return str(template or "").format_map(_SafeDict(values))


def resolve_bug(bug_id: str):
    """把 bug_id 解析成「工作区 + 命令」一整套配置；不符合 target.bug_id_pattern 就返回 None。

    占位符来源：bug_id_pattern 的命名分组 + {tool} / {workspace_root} / {bug_id} / {workspace}。
    返回 dict：bug_id(规范名) / project / number / version / suffix / workspace / setup_cmd / test_cmd
    """
    match = BUG_ID_RE.match((bug_id or "").strip())
    if not match:
        return None

    values = dict(match.groupdict())
    values["bug_id"] = match.group(0)
    values["tool"] = TOOL_PATH
    values["workspace_root"] = WORKSPACE_ROOT

    # version：分组里直接给了就用，否则 number + (suffix 或默认后缀)
    if not values.get("version"):
        suffix = values.get("suffix") or TARGET["default_version_suffix"] or ""
        values["version"] = f"{values.get('number') or ''}{suffix}"

    # 规范名：默认 {project}_{version}（Lang_1 -> Lang_1b）；不想改名就把 bug_id_template 设成 {bug_id}
    canonical = safe_format(TARGET["bug_id_template"], values)
    values["bug_id"] = match.group(0) if "{" in canonical else canonical

    workspace = safe_format(TARGET["workspace_template"], values)

    # 命令里的路径统一做 shell 转义，避免空格/特殊字符把命令拆坏
    cmd_values = dict(values)
    cmd_values["workspace"] = shlex.quote(workspace)
    cmd_values["workspace_root"] = shlex.quote(WORKSPACE_ROOT)
    cmd_values["tool"] = shlex.quote(TOOL_PATH) if TOOL_PATH else ""

    return {
        "bug_id": values["bug_id"],
        "project": values.get("project") or "",
        "number": values.get("number") or "",
        "version": values.get("version") or "",
        "suffix": values.get("suffix") or "",
        "workspace": workspace,
        "setup_cmd": safe_format(TARGET["setup_cmd"], cmd_values).strip(),
        "test_cmd": safe_format(TARGET["test_cmd"], cmd_values).strip(),
    }


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


# ==========================================
# 【防作弊 P0-1】patch 路径白名单
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


def check_patch_path(wsl_path: str):
    """判断这个路径能否被 patch，返回 (ok, 拒绝原因)。

    三层判定，任一不通过就拒绝：
      1. 命中 patch_deny_globs（测试代码）→ 拒绝
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
# 【防作弊 P0-2】测试文件哈希校验
# ==========================================
def _guard_globs():
    """当前生效的受保护文件通配符；返回空列表表示本 Bug 不校验。"""
    if not TARGET.get("test_guard_enabled", True):
        return []
    return _glob_list(TARGET.get("test_guard_globs"))


def guard_scan(workspace: str):
    """扫出受保护文件的 {路径: md5}；未启用或工作区不可读时返回 None。"""
    globs = _guard_globs()
    if not globs or not workspace:
        return None

    preds = " -o ".join(f"-path {shlex.quote(g)}" for g in globs)
    cmd = (
        f"test -d {shlex.quote(workspace)} || exit 3; "
        f"find {shlex.quote(workspace)} -type f \\( {preds} \\) -print0 2>/dev/null "
        f"| sort -z | xargs -0 -r md5sum"
    )
    res = execute_in_target(cmd)
    if res.returncode != 0:
        return None

    table = {}
    for line in (res.stdout or "").splitlines():
        digest, sep, path = line.partition("  ")
        if sep and digest.strip() and path.strip():
            table[path.strip()] = digest.strip()
    return table


def init_test_guard(workspace: str):
    """checkout 之后立刻给测试文件拍基线（此后的任何改动都算作弊）。"""
    global TEST_GUARD_BASELINE, TEST_GUARD_EMPTY
    table = guard_scan(workspace)
    if table is None:
        TEST_GUARD_BASELINE = None
        TEST_GUARD_EMPTY = False
        logging.info("[guard] 测试文件哈希校验未启用（test_guard_globs 为空或工作区不可读）")
        return

    TEST_GUARD_BASELINE = table
    TEST_GUARD_EMPTY = not table
    logging.info(f"[guard] {CURRENT_BUG} 测试文件基线已建立：{len(table)} 个文件")
    if not table:
        logging.error("[guard] 基线为空：项目布局与 test_guard_globs 不匹配，本 Bug 将拒绝运行测试（fail-fast）")


def verify_test_guard():
    """跑测试前比对测试文件哈希，返回 (ok, 说明)；不通过时按需自动还原。"""
    if not _guard_globs():
        return True, ""
    if TEST_GUARD_EMPTY:
        # fail-fast：基线为空等于 guard 全程空转，宁可不跑，也别产出无法审计的 Passed
        return False, (
            "【配置错误】test_guard_globs 没有匹配到任何文件，测试文件哈希校验无法生效，已拒绝运行测试。\n"
            "请修正 config.json 的 target.test_guard_globs，或显式设置 test_guard_enabled=false。"
        )
    if TEST_GUARD_BASELINE is None:
        # 服务重启 / 断点续跑会丢基线，这里补拍一张，避免误报
        init_test_guard(CURRENT_WORKDIR)
        if TEST_GUARD_BASELINE is None:
            return True, ""

    current = guard_scan(CURRENT_WORKDIR)
    if current is None:
        return True, ""

    changed = [p for p in TEST_GUARD_BASELINE if current.get(p) != TEST_GUARD_BASELINE[p]]
    added = [p for p in current if p not in TEST_GUARD_BASELINE]
    if not changed and not added:
        return True, ""

    detail = "\n".join(
        [f"  - 修改/删除：{p}" for p in sorted(changed)]
        + [f"  - 新增：{p}" for p in sorted(added)[:20]]
    )
    logging.warning(f"[guard] {CURRENT_BUG} 测试文件被改动：改/删 {len(changed)} 个，新增 {len(added)} 个")

    if not TARGET.get("test_guard_autorestore", True):
        return False, (
            "【防作弊拦截】测试文件哈希与基线不一致，已拒绝运行测试：\n"
            f"{detail}\n修复只能通过修改被测源码完成。"
        )

    steps = []
    if changed:
        steps.append("git checkout -- " + " ".join(shlex.quote(p) for p in changed))
    if added:
        steps.append("rm -f " + " ".join(shlex.quote(p) for p in added))
    restore_cmd = f"cd {shlex.quote(CURRENT_WORKDIR or '')} && " + " && ".join(steps)
    try:
        restore_res = execute_in_target(restore_cmd)
    except Exception as err:
        return False, (
            "【防作弊拦截】测试文件哈希与基线不一致，自动还原时异常，已拒绝运行测试：\n"
            f"{detail}\n异常：{err}"
        )

    if guard_scan(CURRENT_WORKDIR) == TEST_GUARD_BASELINE:
        logging.warning("[guard] 测试文件已被自动还原")
        return False, (
            "【防作弊拦截】检测到测试代码被修改，系统已自动还原到基线，本轮测试被拒绝运行。\n"
            f"{detail}\n"
            "请重新调用 run_test（还原后的代码需要重新验证），之后只改被测源码。"
        )

    return False, (
        "【防作弊拦截】测试文件哈希与基线不一致，且自动还原失败（git 退出码 "
        f"{restore_res.returncode}），已拒绝运行测试：\n{detail}\n"
        f"还原输出：{((restore_res.stdout or '') + (restore_res.stderr or ''))[-800:]}"
    )


def guard_strike(reason_text: str):
    """防作弊违规计数：同一 Bug 累计到上限就熔断，别让 agent 把 100 轮全花在试探边界上。"""
    global GUARD_VIOLATIONS
    GUARD_VIOLATIONS += 1
    logging.warning(f"[guard] {CURRENT_BUG} 违规第 {GUARD_VIOLATIONS} 次（上限 {GUARD_VIOLATION_LIMIT}）")

    if GUARD_VIOLATIONS >= GUARD_VIOLATION_LIMIT:
        fused_text = (
            reason_text
            + f"\n\n【系统强制熔断】已累计 {GUARD_VIOLATIONS} 次防作弊违规，本 Bug 记为失败。请立刻停止修复并输出 [FAILED]。"
        )
        logging.error(f"[guard] {CURRENT_BUG} 熔断：累计 {GUARD_VIOLATIONS} 次违规")
        reset_task_state("Failed")
        return True, fused_text

    return False, reason_text + f"\n（防作弊违规 {GUARD_VIOLATIONS}/{GUARD_VIOLATION_LIMIT} 次，达上限将直接熔断本任务。）"


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
# 任务结束复位（指标由客户端 run_benchmark.py 记录）
# ==========================================
def reset_task_state(status: str):
    """一次任务走到终点（测试通过 / 超时 / 达到最大尝试次数）后复位状态锁。

    基准指标（含 token 用量、轮次、耗时）由 run_benchmark.py 汇总后写入
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
# 【run_test 结果回写】work_log 里只保留最新一次测试结果
# 目的：上下文被压缩后，agent 调 get_work_log 就能看到上一次测试结果，
#       不必再白跑一次 run_test
# 约束：不新增任何记录行、不删除任何记录行，只往【最新一条工作记录】上挂 test_result 字段，
#       因此 rollback 按 round 截断日志 / 按 changes 回滚文件的逻辑完全不受影响。
# ==========================================
TEST_RECORD_TAIL_CHARS = 2000   # 落盘只保留尾部，防止 work_log.jsonl 被超长堆栈撑爆


def save_test_result(test_status: str, fail_count=None, detail: str = ""):
    """把最近一次 run_test 的结果挂到 work_log.jsonl 里【最新的一条工作记录】上。

    - 全文件只保留最新一次测试结果：先清掉所有历史 test_result 字段，再写本次的；
    - 没有任何工作记录就什么都不存；
    - 只改字段，不增删记录行；坏行原样保留，因此 rollback 的功能不受影响；
    - 任何写盘异常只记日志，绝不打断 run_test 的返回。
    """
    if WORK_JSON_FILE is None or not os.path.exists(WORK_JSON_FILE):
        return
    try:
        with open(WORK_JSON_FILE, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]

        # (原始文本, 解析后的 dict)；解析失败的行 raw 非 None，原样保留
        records = []
        for line in lines:
            try:
                records.append((None, json.loads(line)))
            except json.JSONDecodeError:
                records.append((line if line.endswith("\n") else line + "\n", None))

        # 倒序找到最后一条可解析的工作记录作为挂载点
        target_idx = None
        for idx in range(len(records) - 1, -1, -1):
            if records[idx][1] is not None:
                target_idx = idx
                break
        if target_idx is None:
            return  # 没有工作记录可以挂，那就不保存

        for idx, (raw, data) in enumerate(records):
            if data is None:
                continue
            data.pop("test_result", None)   # 历史结果一律丢弃，只留最新一次
            if idx == target_idx:
                data["test_result"] = {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "test_status": test_status,   # Passed / Failed / CompileError / Timeout / EmptyLog / Error / Unknown
                    "fail_count": fail_count,
                    "detail": (detail or "")[-TEST_RECORD_TAIL_CHARS:]
                }

        with open(WORK_JSON_FILE, "w", encoding="utf-8") as f:
            for raw, data in records:
                f.write(raw if data is None else json.dumps(data, ensure_ascii=False) + "\n")
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

server = Server("zerox-server")

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="run_test",
            description="运行当前测试集的测试并【阻塞等待结果】（命令来自 config.json 的 target）。首次调用会先执行 target.setup_cmd 准备工作区，之后调用只跑 target.test_cmd。返回测试日志和结果。",
            inputSchema={
                "type": "object",
                "properties": {
                    "bug_id": {"type": "string", "description": "必填，需符合 config.json 中 target.bug_id_pattern 的规则（如 Lang_1）"},
                    "force_checkout": {"type": "boolean", "description": "如果你需要彻底重置代码到初始状态，传 true。"}
                },
                "required": ["bug_id"]
            }
        ),
        types.Tool(
            name="search_code",
            description="在代码库中搜索关键字（类名、方法名、变量名、报错信息），返回匹配的文件路径和行号。",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "要搜索的关键字，例如 'toLocale'"},
                    "file_pattern": {"type": "string", "description": "可选，限定搜索范围，例如 '*.java'"}
                },
                "required": ["keyword"]
            }
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
        ),
        types.Tool(
            name="get_file_structure",
            description="获取 Java 文件的代码结构（包含类名和方法名及其起始行号）。在你使用 search_code 找到文件后，强烈建议先调用此工具，再精准定位需要读取的行范围。",
            inputSchema={
                "type": "object",
                "properties": {
                    "wsl_file_path": {"type": "string", "description": "Java 文件的绝对路径"}
                },
                "required": ["wsl_file_path"]
            }
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    global current_log_handle, current_process, WORK_JSON_FILE, TEST_LOG_FILE, CURRENT_BUG
    global IS_TASK_INITIALIZED, START_TIME, CURRENT_ROUND, TOTAL_ATTEMPTS, READ_CODE_COUNT
    global CODE_MODIFIED, CURRENT_WORKDIR, TEST_GUARD_BASELINE, TASK_FINISHED, GUARD_VIOLATIONS

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
        
        # 判断是否需要重新 checkout
        # 条件：强制，或没初始化，或换了另一个 Bug
        need_checkout = force_checkout or (not IS_TASK_INITIALIZED) or (CURRENT_BUG != new_bug_id)

        if not force_checkout and CURRENT_BUG is not None and CURRENT_BUG == new_bug_id:
            # 【P0 去重】重复调用必须在 checkout 之前拦住：
            # reset_task_state() 会把 IS_TASK_INITIALIZED 置 False，不加这层就会再跑一次全量 checkout，
            # 把已经修好的工作区打回 buggy 状态（现象就是 run_test 被连续调用两次，第二次白跑一遍）。
            if TASK_FINISHED:
                return [types.TextContent(type="text", text="【无需重复测试】该 Bug 上一次 run_test 已经全部通过，工作区仍是修复后的状态。请直接输出 [FIXED] 结束任务；如果后来又改了代码，请先调用 `patch_and_log` 记录新一轮，再跑测试。", isError=True)]
            if not CODE_MODIFIED:
                return [types.TextContent(type="text", text="【系统强制拒绝】代码没有被修改过，重复运行测试没有任何意义！请先调用 `patch_and_log` 提交你的修改猜想！（上一次 run_test 的结果已经写在 work_log.jsonl 里，可直接调用 `get_work_log` 查看，无需再跑一次测试。）", isError=True)]

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
            CODE_MODIFIED = False
            TASK_FINISHED = False
            GUARD_VIOLATIONS = 0
            
            logging.info(f"切换当前 Bug 为 {CURRENT_BUG} | 执行全量 Checkout")

            # 【防作弊 P0-2】checkout 与测试必须分两步跑：中间要拍一张测试文件基线哈希。
            # setup_cmd 为空（工作区已存在、无需重置）时直接跳过准备阶段。
            if setup_cmd:
                try:
                    setup_res = execute_in_target(setup_cmd, timeout=TEST_TIMEOUT)
                except subprocess.TimeoutExpired:
                    msg = f"【环境准备超时】setup_cmd 超过 {TEST_TIMEOUT}s 未结束，已放弃本轮。"
                    save_test_result("SetupFailed", None, msg)
                    # 工作区可能是半成品：标回未初始化，下次 run_test 重新 checkout
                    IS_TASK_INITIALIZED = False
                    TEST_GUARD_BASELINE = None
                    return [types.TextContent(type="text", text=msg, isError=True)]
                if setup_res.returncode != 0:
                    setup_out = ((setup_res.stdout or "") + (setup_res.stderr or "")).strip()
                    msg = f"【环境准备失败】setup_cmd 退出码 {setup_res.returncode}：\n{setup_out[-2000:]}"
                    save_test_result("SetupFailed", None, msg)
                    # 同上：别拿一个损坏的工作区继续跑增量测试
                    IS_TASK_INITIALIZED = False
                    TEST_GUARD_BASELINE = None
                    return [types.TextContent(type="text", text=msg, isError=True)]

            init_test_guard(workspace)
            full_cmd = test_cmd

        else:
            logging.info(f"当前 Bug {CURRENT_BUG} | 执行增量测试")
            # 工作区可能就在当前目录/固定目录，所以 require_workspace 做成可关的
            if TARGET["require_workspace"]:
                full_cmd = f"test -d {wsl_workdir} || {{ echo 'WORKDIR_NOT_FOUND: 指定的目录不存在'; exit 1; }}; {test_cmd}"
            else:
                full_cmd = test_cmd


        # 【防作弊 P0-2】跑测试前确认测试代码没被动过，被动过就拒绝运行（不产出分数）
        guard_ok, guard_msg = verify_test_guard()
        if not guard_ok:
            if "【配置错误】" in guard_msg:
                # 配置问题不是 agent 的错，不计入违规次数，也不熔断
                save_test_result("GuardMisconfigured", None, guard_msg)
                return [types.TextContent(type="text", text=guard_msg, isError=True)]
            _fused, guard_msg = guard_strike(guard_msg)
            save_test_result("TestTampered", None, guard_msg)
            return [types.TextContent(type="text", text=guard_msg, isError=True)]

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

            CODE_MODIFIED = False

            logging.info(f"开始阻塞等待测试进程结束（超时限制 {TEST_TIMEOUT}s）...")
            try:
                current_process.wait(timeout=TEST_TIMEOUT)
            except subprocess.TimeoutExpired:
                current_process.kill()
                # 超时没有结论，补丁仍处于「已改未验证」状态：允许重跑一次（否则会被去重逻辑拦住）
                CODE_MODIFIED = True
                save_test_result("Timeout", None, f"【测试超时】执行超过 {TEST_TIMEOUT} 秒仍未结束，进程已被强制终止。")
                reset_task_state("Timeout")
                # 超时不能把补丁一起丢掉：保持「已检出 + 已修改」，下一次 run_test 走增量而不是重新 checkout
                IS_TASK_INITIALIZED = True
                return [types.TextContent(type="text", text=f"【测试超时】执行超过 {TEST_TIMEOUT} 秒仍未结束，进程已被强制终止。", isError=True)]

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
                TASK_FINISHED = True
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
                return [types.TextContent(type="text", text=fail_msg)]

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

            nudge_msg = ""
            if READ_CODE_COUNT >= 5:
                nudge_msg = f"\n\n⚠️ **[System Nudge]** You have read the code {READ_CODE_COUNT} times consecutively. Please stop reading and try to call `patch_and_log` to make a bold hypothesis now!"   
            return [types.TextContent(type="text", text=f"文件 {wsl_path} 第 {start_line}-{end_line} 行内容如下:\n" + "\n".join(formatted_lines) + nudge_msg)]
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
            return [types.TextContent(type="text", text="【系统拒绝】请先调用 run_defects4j_test 初始化当前 Bug 的测试环境。", isError=True)]
        
        if TOTAL_ATTEMPTS >= MAX_ATTEMPTS:
            reset_task_state("Failed") # 直接记为失败
            return [types.TextContent(type="text", text=f"【任务失败】已达最大尝试次数 ({MAX_ATTEMPTS} 次)。请立刻停止修复，并输出 [FAILED]。", isError=True)]
        
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

        # 【防作弊 P0-1】落盘前先做路径白名单校验：测试代码 / 工作区外的文件一律拒绝
        blocked = []
        for change in changes:
            _ok, _why = check_patch_path(change.get("file_path"))
            if not _ok:
                blocked.append(_why)
        if blocked:
            logging.warning(f"[guard] {CURRENT_BUG} 拒绝越界 patch：{blocked}")
            _fused, reject_text = guard_strike(
                "【系统拒绝】patch 白名单校验未通过，以下路径不允许修改：\n- "
                + "\n- ".join(blocked)
                + "\n只允许修改被测源码（见 config.json 的 target.patch_allow_globs / patch_deny_globs）。本轮修改未落盘。"
            )
            return [types.TextContent(type="text", text=reject_text, isError=True)]

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

        # 【只保留最新一次测试结果】写新一轮补丁记录前，先把历史记录里的 test_result 清掉：
        history_lines = []
        if os.path.exists(WORK_JSON_FILE):
            with open(WORK_JSON_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        old_data = json.loads(line)
                    except json.JSONDecodeError:
                        history_lines.append(line if line.endswith("\n") else line + "\n")  # 坏行原样保留
                        continue
                    old_data.pop("test_result", None)
                    history_lines.append(json.dumps(old_data, ensure_ascii=False) + "\n")

        with open(WORK_JSON_FILE, "w", encoding="utf-8") as f:
            f.writelines(history_lines)
            f.write(json.dumps(log_data, ensure_ascii=False) + "\n")

        # 更新全局轮次变量，供指标记录使用
        CURRENT_ROUND = round_num
        
        TOTAL_ATTEMPTS += 1
        READ_CODE_COUNT = 0
        CODE_MODIFIED = True
        # 修复后又打了新补丁：把任务重新打开，否则下一轮 run_test 会被「已修复」拦住
        TASK_FINISHED = False
        return [types.TextContent(type="text", text=f"Patch applied successfully. IMMEDIATELY call run_test to verify. Do NOT speak.")]

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
            else: 
                bug_dir = os.path.dirname(WORK_JSON_FILE)
                audit_file = os.path.join(bug_dir, "human_audit.jsonl")
                with open(audit_file, "a", encoding="utf-8") as f_audit:
                    # 增加回滚标记，方便复盘
                    log_data["rollback_target"] = target_round
                    f_audit.write(json.dumps(log_data, ensure_ascii=False) + "\n")

        with open(WORK_JSON_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

        return [types.TextContent(type="text", text=f"回滚成功！已撤销当前 Bug（{CURRENT_BUG}）第 {target_round} 轮及之后的修改，恢复了 {len(restored_files)} 个文件到修改前的状态。你可以调用 get_work_log 确认当前的历史状态。")]

async def main():
    logging.info("🔥 MCP Server 启动！")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
