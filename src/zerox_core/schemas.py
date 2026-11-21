"""工具 schema。

纯数据，不含任何逻辑：改描述文案就在这里改，对模型可见的字段名一律不换。
"""
from mcp import types


def build_tools():
    return [
        types.Tool(
            name="run_test",
            description="运行当前测试集的测试并【阻塞等待结果】。首次调用会先执行 target.setup_cmd 准备工作区，之后调用只跑 target.test_cmd。返回测试日志和结果。",
            inputSchema={
                "type": "object",
                "properties": {
                    "bug_id": {"type": "string", "description": "必填，需符合 config.json 中 target.bug_id_pattern 的规则（如 Lang_1）"},
                    "force_checkout": {"type": "boolean", "description": "传 true 会重新检出工作区并删除当前 work_log（历史推理全部丢失），同一 bug 上不重置测试次数。仅在工作区状态不可信时使用。"}
                },
                "required": ["bug_id"]
            }
        ),
        types.Tool(
            name="search_code",
            description="在代码库中搜索关键字（类名、方法名、变量名、报错信息），返回文件路径和行号。默认按字面匹配（[] . () 无需转义），结果按定义行优先排序、路径相对工作区。",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "要搜索的关键字，按字面文本解释，例如 'public int[] add'；需要正则时传 regex=true"},
                    "file_pattern": {"type": "string", "description": "可选，只匹配文件名（如 '*.java'），也可带工作区内目录前缀（如 'src/main/java/*.java'）"},
                    "regex": {"type": "boolean", "description": "可选，默认 false 按字面匹配；传 true 时 keyword 按扩展正则解释"},
                    "max_results": {"type": "integer", "description": "可选，最多返回多少条结果（1-100，默认 20）"}
                },
                "required": ["keyword"]
            }
        ),
        types.Tool(
            name="read_specific_code",
            description="读取代码文件。传 start_line 与 end_line 时只返回该范围（带行号）；省略则返回全文，并提示总行数。",
            inputSchema={
                "type": "object",
                "properties": {
                    "wsl_file_path": {"type": "string", "description": "文件的绝对路径，或工作区内的相对路径（如 src/main/java/...）"},
                    "start_line": {"type": "integer", "description": "可选，读取的起始行（从1开始）"},
                    "end_line": {"type": "integer", "description": "可选，读取的结束行"}
                },
                "required": ["wsl_file_path"]
            }
        ),
        types.Tool(
            name="patch_and_log",
            description="核心工具：将修改方案持久化到日志，并自动把补丁应用到目标文件。每次修改代码必须调用此工具，严禁输出全量代码。patch_diff 用行号锚点：给 start_line + anchor + new，服务端按锚点定位替换，无需抄写含缩进的原文，省 token 且不易出错。",
            inputSchema={
                "type": "object",
                "properties": {
                    "reasoning": {"type": "string", "description": "你对报错原因的推断（越精炼越好）"},
                    "fix_summary": {"type": "string", "description": "简明扼要说明你的改动（50字以内）"},
                    "changes": {
                        "type": "array",
                        "description": "本轮修改涉及的文件和代码差异列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file_path": {"type": "string", "description": "你修改了哪个文件"},
                                "patch_diff": {
                                    "type": "object",
                                    "description": "行号锚点补丁：start_line/anchor/new（可加 end_line 改多行，op=delete 删行）。",
                                    "properties": {
                                        "start_line": {"type": "integer", "description": "要修改的起始行号（从1开始，来自 read_specific_code 返回的行号）"},
                                        "end_line": {"type": "integer", "description": "结束行号（含），省略则只改 start_line 一行"},
                                        "anchor": {"type": "string", "description": "起始行上的一段原文（至少8字符），用于校验行号；不用带缩进"},
                                        "op": {"type": "string", "enum": ["replace", "delete", "insert"], "description": "replace=替换（默认），delete=删除该行范围，insert=在 start_line 前插入"},
                                        "new": {"type": "string", "description": "替换/插入的新内容；delete 时不需要"}
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
            description="可选的详细历史视图：每轮的完整推理、最近一次测试详情。每轮注入的 LIVE STATE 状态卡已覆盖轮次、失败数趋势、当前推断与已排除方向——仅当状态卡缺少你需要的具体细节时才调用它。",
            inputSchema={
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "读取最近几轮的日志，默认3"}},
                "required": []
            }
        ),
        types.Tool(
            name="rollback",
            description=(
                "撤销你的修改，并把撤销原因记进工作日志。想验证某个值就插一条 System.err.println 探针，"
                "跑完测试后用 rollback 撤掉；确认某条修复方向错了也用它退回来。"
                "注意 target_round 是「撤销第 N 轮及之后」，所以撤一条探针就把 N 写成探针那一轮，"
                "写小了会把前面的真修复一起撤销。reasoning 必填：被撤销的轮次会连同它的 reasoning "
                "一起归档，你之后看不到了，只有这条 reasoning 会留在存活日志里。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target_round": {
                        "type": "integer",
                        "description": "撤销第 N 轮及之后的全部修改，保留第 N-1 轮。传 1 表示撤销所有修改、回到初始状态。"
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "为什么撤销：探针验证到了什么、哪条假设被证伪。写清结论，不要写「没用」。"
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["probe", "deadend"],
                        "description": "probe=撤销探针（测试本身仍失败）；deadend=这条修复方向错了。默认 deadend。"
                    }
                },
                "required": ["target_round", "reasoning"]
            }
        ),
        types.Tool(
            name="get_file_internal_structure",
            description="获取 Java 文件的结构索引：类/接口/枚举、方法、构造函数的签名（含参数类型）与 [起始行-结束行] 范围。包含包级私有方法，同名方法靠参数类型区分，起止行可直接作为 read_specific_code 的读取范围或锚点补丁的行号。用 search_code 找到文件后建议先调用此工具。路径用相对路径或绝对路径均可。",
            inputSchema={
                "type": "object",
                "properties": {
                    "wsl_file_path": {"type": "string", "description": "Java 文件的绝对路径"}
                },
                "required": ["wsl_file_path"]
            }
        ),
    ]
