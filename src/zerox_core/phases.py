"""首轮工具集裁剪：仅 INIT 与 READY 两个阶段。

依据：工具 schema 每轮随请求重发，是固定前缀中占比最高的一段（7 个工具约 3.2K 字符）。
但 tools 数组一旦变化，服务端前缀缓存自变化处起失效：同一段对话实测，工作集固定时第 3
次请求命中 2,432 token，按动作交替两套工作集时命中 1,536 token，切换后的首次请求仅 8%。
26 题批次共发生 92 次阶段切换，其重算成本超过裁剪 schema 所节省的 token，净计费反而上升。

因此裁剪只作用于第一次请求：该次请求的缓存必然为空，切换不产生额外成本。
    INIT   尚无测试结论 —— 仅暴露 run_test。服务端 search / read 本就要求先由
           run_test 初始化工作区，此处将同一约束提前告知模型。
    READY  取得测试结论之后 —— 七个工具全量发送，一次设定、全程不变，缓存无断点。

INIT_TOOLS 是 READY_TOOLS 的前缀（run_test 位列第一），故第一次请求的 tools 段可在
第二次请求中继续命中，切换并非全冷。

阶段推进仅发生在取得有效测试结论时：run_test 返回 setup 失败或执行异常（isError）时
工作区状态不可信，唯一有意义的动作是重跑测试，而 INIT 阶段仅暴露 run_test。

get_work_log 不在 INIT 的原因有两点：其一，服务端在第一次 run_test 之前 WORK_JSON_FILE
仍为 None，调用会被直接拒绝（zerox_server.py:1035），续跑的 resume_from_worklog 同样
只在 run_test 内执行；其二，续跑不需要该入口——LIVE STATE 状态卡每轮直接读取
work_log.jsonl 重建（zerox.py:214），首轮即包含恢复后的假设、测试趋势与已排除方向。

纯函数、无运行期状态，可离线单测（tools/selftests/phases_selftest.py）。
"""

INIT = "init"
READY = "ready"

INIT_TOOLS = ("run_test",)

# 顺序为固定常量：同一集合须序列化为同一段字符串，否则缓存前缀会因顺序差异失效。
# run_test 置于首位，使 INIT 的 tools 段成为该元组的前缀。
READY_TOOLS = (
    "run_test",
    "search_code",
    "get_file_internal_structure",
    "read_specific_code",
    "get_work_log",
    "rollback",
    "patch_and_log",
)

_BY_PHASE = {INIT: INIT_TOOLS, READY: READY_TOOLS}


def tools_for(phase):
    """该阶段允许出现在 tools 数组里的工具名（保序，未知阶段按 READY 兜底）。"""
    return _BY_PHASE.get(phase, READY_TOOLS)


def next_phase(phase, tool_name, ok=True):
    """一次工具调用之后的阶段。

    全流程仅一次转移：INIT 中成功的 run_test → READY。此后补丁、测试、回滚均停留在
    READY——工具集不随动作变化是该设计的核心，任何中途切换都会使服务端前缀缓存失效，
    其重算成本高于裁剪 schema 的收益。

    ok=False 表示 run_test 未取得结论（setup 失败 / 执行异常）：工作区状态不可信，
    停留于 INIT 重跑测试。
    """
    if phase == INIT and tool_name == "run_test" and ok:
        return READY
    return phase
