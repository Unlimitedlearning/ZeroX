"""工具 schema。

纯数据，不含任何逻辑：改描述文案就在这里改，对模型可见的字段名一律不换。

【描述从简】tools 数组每轮随请求重发，此处每个字都计入「每次请求都要重付」的固定前缀
（7 个工具的 schema 曾占 4.2K 字符，与 AGENTS.md 相当）。因此：
  - 仅写「何时使用 + 必须知道的语义」，不写教程、动机，也不写「这样省 token」；
  - 使用指引（失败后如何修改、参数错误如何处理）由服务端的错误文本承担：错误只在发生
    时付费一次，其成本比每轮重复发送低一个量级（见 patcher._LINE_ANCHOR_ERRORS）；
  - 客户端注入的控制参数（force_checkout / resume）不予声明：run_test 的入参只有
    bug_id，工作区重置与续跑由客户端决定，模型无权发起，也不应为其承担 schema 成本。
新增描述前先判断：该信息能否由工具的错误返回承担；可以则不写入 schema。
"""
from mcp import types


def build_tools():
    return [
        types.Tool(
            name="run_test",
            description="运行测试并阻塞等待结果。首次调用会准备工作区（checkout）。返回失败用例、源位置、方法体与断言。",
            inputSchema={
                "type": "object",
                "properties": {
                    "bug_id": {"type": "string", "description": "Bug 编号，如 Lang_1"}
                },
                "required": ["bug_id"]
            }
        ),
        types.Tool(
            name="search_code",
            description="在代码库中搜索关键字，返回文件路径、行号与所在行原文，定义行优先。",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "要搜索的文本，按字面匹配；regex=true 时按正则解释"},
                    "file_pattern": {"type": "string", "description": "可选，文件名或带目录的过滤，如 src/main/java/*.java"},
                    "regex": {"type": "boolean", "description": "可选，默认 false"},
                    "max_results": {"type": "integer", "description": "可选，默认 20，上限 100"}
                },
                "required": ["keyword"]
            }
        ),
        types.Tool(
            name="read_specific_code",
            description="按行范围读取代码，带行号。省略行号则返回全文。",
            inputSchema={
                "type": "object",
                "properties": {
                    "wsl_file_path": {"type": "string", "description": "绝对路径，或工作区内相对路径"},
                    "start_line": {"type": "integer", "description": "起始行（从 1 开始）"},
                    "end_line": {"type": "integer", "description": "结束行（含）"}
                },
                "required": ["wsl_file_path"]
            }
        ),
        types.Tool(
            name="patch_and_log",
            description="应用补丁并记录推理。行号锚点：start_line + anchor + new，不需要抄写原文。",
            inputSchema={
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string", "description": "对报错原因的推断，越精炼越好"},
                    "fix_summary": {"type": "string", "description": "50 字内的改动说明"},
                    "changes": {
                        "type": "array",
                        "description": "本轮改动的文件与差异",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string", "description": "要修改的文件"},
                                "patch_diff": {
                                    "type": "object",
                                    "description": "行号锚点补丁",
                                    "properties": {
                                        "start_line": {"type": "integer", "description": "起始行号（来自 read_specific_code）"},
                                        "end_line": {"type": "integer", "description": "结束行号（含），省略则只改一行"},
                                        "anchor": {"type": "string", "description": "起始行上一段不少于 8 字符的原文，用于校验"},
                                        "op": {"type": "string", "enum": ["replace", "delete", "insert"], "description": "replace（默认）/ delete / insert（插在 start_line 之前）"},
                                        "new": {"type": "string", "description": "替换或插入的内容；delete 时不需要"}
                                    },
                                    "required": ["start_line", "anchor"]
                                }
                            },
                            "required": ["file_path", "patch_diff"]
                        }
                    }
                },
                "required": ["reasoning", "fix_summary", "changes"]
            }
        ),
        types.Tool(
            name="get_work_log",
            description="可选的详细历史：每轮完整推理与最近一次测试详情。LIVE STATE 未覆盖的细节才用它。",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "最近几轮，默认 3"}},
                "required": []
            }
        ),
        types.Tool(
            name="rollback",
            description="撤销第 N 轮及之后的修改，并把撤销理由写进日志。验证探针或放弃方向时用它。",
            inputSchema={
                "type": "object",
                "properties": {
                    "target_round": {"type": "integer", "description": "撤销第 N 轮及之后，保留第 N-1 轮；传 1 = 撤销全部"},
                    "reasoning": {"type": "string", "description": "为什么撤销：探针验证到了什么、哪条假设被证伪"},
                    "kind": {"type": "string", "enum": ["probe", "deadend"], "description": "probe=撤探针；deadend=方向失败（默认）"}
                },
                "required": ["target_round", "reasoning"]
            }
        ),
        types.Tool(
            name="get_file_internal_structure",
            description="返回 Java 文件的类/方法签名与 [起-止] 行号。先看结构再按范围读，比读整个文件省上下文。",
            inputSchema={
                "type": "object",
                "properties": {
                    "wsl_file_path": {"type": "string", "description": "绝对路径，或工作区内相对路径"}
                },
                "required": ["wsl_file_path"]
            }
        ),
    ]
