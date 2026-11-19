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
            description="在代码库中搜索关键字（类名、方法名、变量名、报错信息），返回匹配的文件路径和行号。",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "要搜索的关键字，例如 'toLocale'"},
                    "file_pattern": {"type": "string", "description": "可选，只匹配文件名（不含目录），例如 '*.java'"}
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
            description="核心工具：将修改方案持久化到日志，并自动把代码 Diff 应用到目标文件。每次修改代码必须调用此工具，严禁输出全量代码。old 片段须与文件精确匹配且在文件中唯一出现，否则整轮被拒。",
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
                "required": ["reasoning", "fix_summary", "changes"]
            }
        ),
        types.Tool(
            name="get_work_log",
            description="读取工作日志：每轮改了什么、失败用例数的变化、已撤销方向的原因。上下文被压缩后用它恢复状态。",
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
