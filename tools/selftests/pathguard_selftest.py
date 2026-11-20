"""读工具工作区边界自检：不连 WSL、不跑测试，只验纯解析函数。

    python tools/selftests/pathguard_selftest.py

覆盖：
  1. 工作区内相对路径 -> 通过，且解析成绝对路径
  2. 工作区内绝对路径 -> 通过
  3. 工作区外绝对路径（case/pristine 基线）-> 拒绝（这就是本次修复的核心）
  4. .. 逃出工作区 -> 拒绝；.. 但仍在工作区内 -> 通过（读和 patch 一致）
  5. 前缀边界：/ws/work2 不能被当成 /ws/work 的内部路径
  6. Windows 反斜杠路径 -> 正确归一化
  7. 未初始化 / 空路径 -> 拒绝
  8. search_code 传入的相对前缀（src/main/java）-> 通过
  9. patch 的 allow/deny globs 匹配的是归一化后的绝对路径，不能被 .. 绕过
  10. display_path 回显相对路径，且该相对路径能被 resolve_read_path 原样读回

已知不在覆盖内：符号链接指向工作区外（见 README 的路径约束说明）。
"""
import os
import sys

# tools/selftests/ 比 tools/ 深一层，剥三次 dirname 拿项目根；代码在 src/ 下
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.paths import (  # noqa: E402
    display_path,
    normalize_under_workspace,
    resolve_patch_path,
    resolve_read_path,
)

WORK = "/home/user/zerox/case/work/List_999"


def check(cond, message):
    if not cond:
        raise AssertionError(message)


def ok(path, work=WORK):
    good, value = resolve_read_path(path, work)
    check(good, f"期望通过但被拒绝：{path} -> {value}")
    return value


def rejected(path, work=WORK):
    good, value = resolve_read_path(path, work)
    check(not good, f"期望拒绝但通过了：{path} -> {value}")
    check("不在当前工作区" in value or "尚未初始化" in value or "路径为空" in value,
          f"拒绝原因不友好：{path} -> {value}")


def main():
    # 1. 工作区内相对路径
    check(ok("src/main/java/Foo.java") == WORK + "/src/main/java/Foo.java",
          "相对路径应拼成工作区内的绝对路径")

    # 2. 工作区内绝对路径（search_code 的 grep -rn 返回的就是绝对路径）
    check(ok(WORK + "/List_999.java") == WORK + "/List_999.java",
          "工作区内绝对路径应原样通过")

    # 3. 核心修复：工作区外的 pristine 基线不能被读工具碰到
    rejected("/home/user/zerox/case/pristine/List_999.java")
    # 相对路径with .. 逃到基线目录，同样拒绝
    rejected("../pristine/List_999.java")

    # 4. .. 的行为：逃出拒绝、仍在内通过
    rejected("src/../../../pristine/List_999.java")
    check(ok("src/main/../main/Foo.java") == WORK + "/src/main/Foo.java",
          "normpath 后仍在工作区内的 .. 应通过")

    # 5. 前缀边界：必须是 workdir 本身或以 workdir + '/' 开头
    rejected("/home/user/zerox/case/work/List_999_evil/Foo.java")
    rejected("/home/user/zerox/case/work/List_9992/Foo.java")

    # 6. 反斜杠（模型偶尔给 Windows 风格路径）
    check(ok("src\\main\\java\\Foo.java") == WORK + "/src/main/java/Foo.java",
          "反斜杠应统一成正斜杠")

    # 7. 未初始化 / 空路径
    rejected("Foo.java", work="")
    rejected("", work=WORK)
    rejected("   ", work=WORK)

    # 8. search_code 的场景：file_pattern 里的相对前缀解析成搜索目录
    check(ok("src/main/java") == WORK + "/src/main/java",
          "search_code 的相对前缀应落在工作区内")

    # normalize_under_workspace 单独也测一下
    check(normalize_under_workspace("Foo.java", WORK + "/") == WORK + "/Foo.java",
          "workdir 尾斜杠不应产生双斜杠")

    # 9. patch 和读工具走同一套归一化：内部的 .. 放行，逃出工作区拒绝
    good, value = resolve_patch_path("src/main/../main/Foo.java", WORK)
    check(good and value == WORK + "/src/main/Foo.java",
          "patch 路径内部 .. 应归一化后放行")
    good, value = resolve_patch_path("../pristine/List_999.java", WORK)
    check(not good and "不在当前工作区" in value,
          "patch 路径逃出工作区必须拒绝")

    # globs 匹配归一化后的路径，不能靠 .. 绕过 deny
    good, value = resolve_patch_path("src/main/../test/Foo.java", WORK,
                                     deny=["*/src/test/*"])
    check(not good and "patch_deny_globs" in value,
          "归一化后的路径仍应命中 deny")
    good, value = resolve_patch_path("src/main/Foo.java", WORK,
                                     allow=["*/src/main/*"])
    check(good, "白名单内的相对路径应通过")
    good, value = resolve_patch_path("src/test/Foo.java", WORK,
                                     allow=["*/src/main/*"])
    check(not good and "patch_allow_globs" in value,
          "白名单外的路径应拒绝")

    # 回显路径相对化：不能泄露 /home/<user>/...，且模型拿它继续调用读工具必须可用
    shown = display_path(f"{WORK}/src/main/java/Foo.java", WORK)
    check(shown == "src/main/java/Foo.java", f"应回显相对路径: {shown}")
    check("/home/" not in shown, f"不应含本地绝对路径: {shown}")
    good, value = resolve_read_path(shown, WORK)
    check(good and value == f"{WORK}/src/main/java/Foo.java",
          f"相对路径应能原样解析回同一文件: {value}")
    check(display_path("/home/user/other/Foo.java", "") == "/home/user/other/Foo.java",
          "未初始化工作区时应原样返回")
    check(display_path(None, WORK) is None, "空路径应原样返回")

    print("pathguard selftest: OK")


if __name__ == "__main__":
    main()
