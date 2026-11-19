"""配置加载与校验。

这里只做「读文件 + 补默认值 + 校验 + 编译正则」这些纯逻辑，不持有任何运行期状态：
默认值与用户配置合并后作为 dict 返回，运行期状态由调用方持有。
"""
import json
import logging
import os
import shlex
import sys
import re

# 同包内的纯函数模块（不反向依赖 config）：路径标准化只写一份，别在两个地方各转一次
from zerox_core.exec_env import win_path_to_wsl


# ==========================================
# 目标环境默认配置：换测试集只改 config.json，代码不用动
# 下面这些默认值与原 Defects4J 行为等价；config.json 里 target / agent 段落的同名字段会覆盖它们。
# 可用占位符：{bug_id} {project} {number} {version} {suffix} {tool} {workspace_root} {workspace}
# ==========================================
DEFAULT_TARGET = {
    # 测试框架/构建工具入口，命令模版里的 {tool}
    "tool_path": "",
    # 所有 bug 工作区的根目录，模版里的 {workspace_root}
    # 支持三种写法（见 resolve_workspace_root）：/mnt/d/... 原样用、D:/... 自动转换、"work" 相对配置文件目录
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
    # ---- 路径约束：运行时只拦「写不进当前工作区」的 patch ----
    # 白名单/黑名单默认都空 = 不限制改哪个文件。想收紧再填，fnmatch 语法；
    # 注意模型常给 src/main/java/... 这类相对路径，规则要能匹配上。
    "patch_allow_globs": [],
    # 命中即拒绝，优先级高于白名单（例如不许改测试数据的备份文件）
    "patch_deny_globs": [],
    # patch 是否必须落在当前工作区内
    "patch_must_be_under_workspace": True,
    # ---- 事后审计：哪些路径算「测试代码」----
    # 只有 audit.py 用这个字段：运行时不做任何拦截，跑完再判定 Fixed / Verified Fixed
    "audit_test_globs": ["*/src/test/*", "*/tests/*", "*/test/*"],
}

DEFAULT_AGENT = {
    # 单个 bug 允许的 run_test 次数上限：服务端唯一的强制限制，达到即终止任务
    "max_tests": 12,
    # ---- 以下是客户端（src/zerox.py）的开关，服务端不读 ----
    # 都是「要经常微调」的默认值：实际生效值由 config.json 的 agent 段落提供，这里仅作兜底
    # 上下文压缩阈值：整个上下文（system + 对话 + 工具输出）的字符数，到了就压缩一次。
    # 按体量比按轮数准：读代码的轮次几乎不涨上下文，一次 3MB 的测试日志却能一把撑爆窗口。
    # 字符→token 大约 ×3~4（代码/英文），中文接近 1:1，阈值需结合模型窗口设定
    "compact_context_chars": 160000,
    # 连续多少次只读调用（中间没有任何补丁/测试）就强制压缩一次。
    # 为什么还需要它：读代码几乎不涨上下文，体量阈值永远抓不到「读 18 轮、
    # 0 个补丁」这种空转（Time_11 压缩时只有 73080 字，差一点就没到）。
    # 0 或 null = 只按体量压缩。
    "compact_idle_reads": 20,
    "system_prompt_file": "AGENTS.md",       # 相对项目根目录（src/ 的上一层），也可以写绝对路径
    "user_prompt_template": "Please fix the bug for {bug_id}. Begin by calling run_test with bug_id='{bug_id}'.",
    "fixed_marker": "[FIXED]",               # agent 输出这个标记就算修复成功
    "failed_marker": "[FAILED]",
}


def load_config(config_file):
    """Fail Fast"""
    if not os.path.exists(config_file):
        logging.error(f"❌ Fatal: Configuration file '{config_file}' not found.")
        logging.error("👉 Please copy 'config.example.json' to 'config.json' and fill in your absolute paths.")
        sys.exit(1)

    try:
        with open(config_file, "r", encoding="utf-8") as f_cfg:
            config = json.load(f_cfg)
    except Exception as err:
        logging.error(f"❌ Fatal: Failed to parse '{config_file}': {err}")
        sys.exit(1)

    for key in ["target", "timeouts"]:
        if key not in config:
            logging.error(f"❌ Fatal: Missing '{key}' in '{config_file}'.")
            sys.exit(1)

    max_tests = (config.get("agent") or {}).get("max_tests", DEFAULT_AGENT["max_tests"])
    if not isinstance(max_tests, int) or max_tests <= 0:
        logging.error(f"❌ Fatal: 'max_tests' must be a positive integer in '{config_file}'.")
        sys.exit(1)

    if not isinstance(config["timeouts"].get("test_execution"), int):
        logging.error(f"❌ Fatal: 'timeouts.test_execution' must be an integer in '{config_file}'.")
        sys.exit(1)

    return config


def resolve_workspace_root(value, base_dir):
    """把 target.workspace_root 解析成【目标环境（WSL / 原生 bash）里的绝对路径】。

    三种写法都支持，这样仓库里不必出现本机绝对路径：
      /mnt/d/work          → 已经是目标环境路径，原样返回（Defects4J 的旧配置不受影响）
      D:\\work  或  D:/work  → 转成 /mnt/d/work
      work  /  ./work      → 相对【配置文件所在目录】拼接，再转成目标环境路径

    为什么必须在这里做：命令都是 bash 跑的，相对路径在 bash 里的 cwd 不可控；
    而把绝对路径写进 config.json，仓库就只在本机能用了（case/config.json 也因此不能用硬编码路径）。
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    # 已经是目标环境路径（注意：Windows 上 os.path.abspath("/mnt/d/x") 会变成 D:\\mnt\\d\\x，不能动它）
    if raw.startswith("/"):
        return raw.rstrip("/") or "/"
    # Windows 盘符路径
    if re.match(r"^[A-Za-z]:[\\/]", raw):
        return win_path_to_wsl(raw).rstrip("/")
    # 相对路径：按配置文件所在目录拼
    return win_path_to_wsl(os.path.join(base_dir, raw)).rstrip("/")


def load_target_config(config, config_file):
    """把 config.json 的 target 段落合并到默认值上；缺字段就用默认值。"""
    target = dict(DEFAULT_TARGET)
    user_target = config.get("target") or {}
    if not isinstance(user_target, dict):
        logging.error(f"❌ Fatal: 'target' must be an object in '{config_file}'.")
        sys.exit(1)
    for key, value in user_target.items():
        if value is not None:
            target[key] = value
    # 解析完默认值就必须统一 workspace_root，否则 server / audit / 客户端三边会各自猜一遍
    base_dir = os.path.dirname(os.path.abspath(config_file))
    target["workspace_root"] = resolve_workspace_root(target.get("workspace_root"), base_dir)
    return target


def load_agent_config(config, config_file):
    """agent 段落（行为开关）单独合并；缺字段就用默认值。"""
    agent_cfg = dict(DEFAULT_AGENT)
    user_agent = config.get("agent") or {}
    if not isinstance(user_agent, dict):
        logging.error(f"❌ Fatal: 'agent' must be an object in '{config_file}'.")
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


def safe_format(template: str, values: dict) -> str:
    """模版渲染：找不到的占位符原样留着，别让少一个键就把工具搞崩。"""
    class _SafeDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    return str(template or "").format_map(_SafeDict(values))


def validate_or_exit(config, target, tool_path, config_file):
    """配置自检：跑不起来就不要到一半才炸。"""
    if not str(target["test_cmd"] or "").strip():
        logging.error(f"❌ Fatal: 'target.test_cmd' cannot be empty in '{config_file}'.")
        sys.exit(1)

    if "{tool}" in f"{target['setup_cmd']} {target['test_cmd']}" and not tool_path:
        logging.error(f"❌ Fatal: 'target.tool_path' is required when commands use {{tool}} in '{config_file}'.")
        sys.exit(1)


def resolve_bug(target: dict, bug_id: str):
    """把 bug_id 解析成「工作区 + 命令」一整套配置；不符合 bug_id_pattern 就返回 None。

    纯函数：只读传进来的 target dict，不碰任何运行期状态，所以事后审计（audit.py）
    能拿同一份 config.json 复现同一个工作区路径和同一条 test_cmd。
    返回 dict：bug_id(规范名) / project / number / version / suffix / workspace / setup_cmd / test_cmd
    """
    pattern = str(target.get("bug_id_pattern") or "")
    try:
        match = re.match(pattern, (bug_id or "").strip())
    except re.error as err:
        logging.error(f"❌ Fatal: 'target.bug_id_pattern' is not a valid regex ({pattern}): {err}")
        return None
    if not match:
        return None

    tool_path = target.get("tool_path") or ""
    workspace_root = (target.get("workspace_root") or "").rstrip("/")

    values = dict(match.groupdict())
    values["bug_id"] = match.group(0)
    values["tool"] = tool_path
    values["workspace_root"] = workspace_root

    # version：分组里直接给了就用，否则 number + (suffix 或默认后缀)
    if not values.get("version"):
        suffix = values.get("suffix") or target.get("default_version_suffix") or ""
        values["version"] = f"{values.get('number') or ''}{suffix}"

    # 规范名：默认 {project}_{version}（Lang_1 -> Lang_1b）；不想改名就把 bug_id_template 设成 {bug_id}
    canonical = safe_format(target.get("bug_id_template"), values)
    values["bug_id"] = match.group(0) if "{" in canonical else canonical

    workspace = safe_format(target.get("workspace_template"), values)

    # 命令里的路径统一做 shell 转义，避免空格/特殊字符把命令拆坏
    cmd_values = dict(values)
    cmd_values["workspace"] = shlex.quote(workspace)
    cmd_values["workspace_root"] = shlex.quote(workspace_root)
    cmd_values["tool"] = shlex.quote(tool_path) if tool_path else ""

    return {
        "bug_id": values["bug_id"],
        "project": values.get("project") or "",
        "number": values.get("number") or "",
        "version": values.get("version") or "",
        "suffix": values.get("suffix") or "",
        "workspace": workspace,
        "setup_cmd": safe_format(target.get("setup_cmd"), cmd_values).strip(),
        "test_cmd": safe_format(target.get("test_cmd"), cmd_values).strip(),
    }
