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
    # ---- 路径约束：运行时只拦「写不进当前工作区」的 patch ----
    # 白名单/黑名单默认都空 = 不限制改哪个文件（往期默认只能改 src/main/java，
    # 结果连 src/main/java/... 这种相对路径都被拒掉）。想收紧再填，fnmatch 语法。
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
    # 单个 bug 允许的 run_test 次数上限：一次测试 3-6 分钟，这是本任务唯一真实的成本
    "max_tests": 12,
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
