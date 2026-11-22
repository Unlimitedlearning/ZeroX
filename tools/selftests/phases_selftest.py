"""阶段自检：不连服务、不调模型，只验 phases 的转移表与 schema 的一致性。

    python tools/selftests/phases_selftest.py

覆盖：
  1. INIT 只给 run_test（服务端 search/read 本来就要求先初始化工作区）
  2. 成功的 run_test 进 READY；没跑出结论（ok=False）停在 INIT 重试
  3. READY 就是全量七个工具，且顺序固定（顺序抖动会让前缀缓存失效）
  4. READY 里补丁 / 测试 / 回滚都不改变阶段——中途切换会把缓存打断
  5. INIT 的工具序列是 READY 的前缀（第一次请求的 tools 段能继续命中）
  6. 未知阶段兜底为 READY（配置/磁盘状态异常时不至于把工具全关掉）
  7. phases 引用的每个工具名都真实存在于 schemas.build_tools()
  8. 七个工具都在 READY 里（不出现「永远拿不到」的死工具）
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.phases import (  # noqa: E402
    INIT, READY, INIT_TOOLS, READY_TOOLS, tools_for, next_phase,
)
from zerox_core.schemas import build_tools  # noqa: E402

# 会改变工作区或推进轮次的三个动作：在 READY 里都必须是「不转移」
DECISION_TOOLS = ("run_test", "patch_and_log", "rollback")


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_init():
    _check(tools_for(INIT) == INIT_TOOLS, "INIT 的工具集合不该被改动")
    _check(tuple(INIT_TOOLS) == ("run_test",),
           f"INIT 只应暴露 run_test，实际 {INIT_TOOLS}")


def test_after_run_test():
    phase = next_phase(INIT, "run_test")
    _check(phase == READY, f"run_test 之后应进 READY，实际 {phase}")
    _check(next_phase(INIT, "run_test", ok=False) == INIT,
           "测试没跑出结论（setup 失败 / 执行异常）时不该进 READY——"
           "工作区还不可信，唯一有意义的动作是重跑测试")


def test_ready_is_full_set():
    declared = tuple(t.name for t in build_tools())
    _check(set(READY_TOOLS) == set(declared),
           f"READY 应等于全量工具集，缺 {set(declared)-set(READY_TOOLS)}，"
           f"多 {set(READY_TOOLS)-set(declared)}")
    _check(len(READY_TOOLS) == len(declared), "READY 里出现了重复工具名")


def test_no_switch_in_ready():
    for name in DECISION_TOOLS:
        _check(next_phase(READY, name) == READY,
               f"{name} 在 READY 里不该改变阶段——改了就要重发一套 tools，缓存从断点失效")
        _check(next_phase(READY, name, ok=False) == READY,
               f"{name} 失败时也不该改变阶段")


def test_init_is_prefix_of_ready():
    _check(tuple(READY_TOOLS[:len(INIT_TOOLS)]) == tuple(INIT_TOOLS),
           "INIT 的工具序列应是 READY 的前缀，让首轮的 tools 段在次轮继续命中缓存")


def test_unknown_phase():
    _check(tools_for("nonsense") == READY_TOOLS, "未知阶段应兜底为 READY")


def test_schema_consistency():
    declared = {tool.name for tool in build_tools()}
    used = set(INIT_TOOLS) | set(READY_TOOLS)
    missing = used - declared
    _check(not missing, f"phases 引用了 schema 里不存在的工具：{sorted(missing)}")
    unreachable = declared - used
    _check(not unreachable, f"这些工具没有任何阶段能拿到：{sorted(unreachable)}")


def main():
    test_init()
    test_after_run_test()
    test_ready_is_full_set()
    test_no_switch_in_ready()
    test_init_is_prefix_of_ready()
    test_unknown_phase()
    test_schema_consistency()
    print("phases selftest: OK")


if __name__ == "__main__":
    main()
