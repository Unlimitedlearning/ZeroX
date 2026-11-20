"""工作区路径解析：读工具和 patch 共用同一套归一化 + 边界判定。

纯函数，不持有运行期状态：workdir 由调用方显式传入（server 侧是 CURRENT_WORKDIR）。
放在这里而不是 zerox_server.py，是因为读工具、patch 校验和离线 selftest 都要用同一套解析，
而 zerox_server 导入时会加载配置、建日志文件，不适合当纯逻辑库 —— 两份解析一旦漂移，
就会出现「读能进但写被拦」或更糟的「写被拦住但读能绕过」这类隔离漏洞。

注意：这里只做字符串层面的收敛，不解析符号链接；指向工作区外的 symlink 仍可逃逸，
已知且刻意不覆盖（见 selftest 的说明）。
"""
import fnmatch
import posixpath


def normalize_under_workspace(raw_path, workdir):
    """相对路径按 workdir 拼成绝对路径，绝对路径原样保留，再统一 normpath。

    normpath 会消解 ..：只要解析结果仍落在 workdir 内就允许（模型偶尔会用
    src/main/../main/Foo.java 这种写法），逃出工作区则被 resolve_*_path 拒绝。
    """
    path = str(raw_path or "").strip().replace("\\", "/")
    if not path:
        return ""
    base = (workdir or "").rstrip("/")
    if base and not path.startswith("/"):
        path = f"{base}/{path}"
    return posixpath.normpath(path)


def is_under_workspace(path, workdir):
    """path 是否就是工作区本身或位于其内部（不能用 startswith(base) 判断前缀边界）。"""
    base = (workdir or "").rstrip("/")
    return bool(base) and (path == base or path.startswith(base + "/"))


def _glob_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(v).strip() for v in value if str(v).strip()]


def _match_any(path, globs):
    """fnmatchcase：Windows 上 fnmatch 会转小写，Test/ 会误命中 */test/*。"""
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def resolve_patch_path(raw_path, workdir, allow=None, deny=None,
                       must_be_under_workspace=True):
    """patch 路径统一入口，返回 (ok, path_or_reason)。

    与读工具共用 normalize_under_workspace：相对路径先拼到工作区，再 normpath 消解
    `..`。因此「工作区内部的 ..」（src/main/../main/Foo.java）是合法写法，只有解析后
    逃出工作区才拒绝 —— 这既和读工具一致，又不会让白名单被 ../ 绕过（glob 匹配的是
    归一化后的绝对路径）。
    """
    path = normalize_under_workspace(raw_path, workdir)
    if not path:
        return False, "file_path 为空"

    deny_globs = _glob_list(deny)
    allow_globs = _glob_list(allow)
    if deny_globs and _match_any(path, deny_globs):
        return False, f"{path} 命中 patch_deny_globs（测试代码/受保护文件禁止修改）"
    if allow_globs and not _match_any(path, allow_globs):
        return False, (f"{path} 不在 patch_allow_globs 白名单内"
                       f"（允许：{', '.join(allow_globs)}）")

    if must_be_under_workspace and not is_under_workspace(path, workdir):
        return False, f"{path} 不在当前工作区 {(workdir or '').rstrip('/')} 内"
    return True, path


def display_path(path, workdir):
    """回给模型的路径一律相对工作区：省 token，也避免本地绝对路径进 transcript。

    resolve_read_path / resolve_patch_path 都接受相对路径（会按 workdir 拼回绝对值），
    所以模型拿到相对路径后继续调用读工具或补丁工具不受影响。workdir 为空时原样返回。
    """
    if not path or not workdir:
        return path
    try:
        return posixpath.relpath(str(path).replace("\\", "/"), str(workdir).rstrip("/"))
    except (TypeError, ValueError):
        return path


def resolve_read_path(raw_path, workdir):
    """解析读工具路径，返回 (ok, value)。

    ok=True  → value 是工作区内的绝对路径，可直接交给目标环境 cat / grep
    ok=False → value 是可原样回给 agent 的拒绝原因

    读不套用 patch 的 allow/deny globs（读是免费、不限路径类型的），但【始终】要求
    落在当前工作区内 —— 这正是 agent 看不到工作区外 pristine 基线的那道边界。
    """
    base = (workdir or "").rstrip("/")
    if not base:
        return False, "【系统拒绝】尚未初始化工作区，请先调用 run_test。"
    path = normalize_under_workspace(raw_path, base)
    if not path:
        return False, "【系统拒绝】路径为空。"
    if not is_under_workspace(path, base):
        return False, (
            f"【系统拒绝】读取路径 {path} 不在当前工作区 {base} 内；"
            "读工具只能访问当前 Bug 的工作区文件。"
        )
    return True, path
