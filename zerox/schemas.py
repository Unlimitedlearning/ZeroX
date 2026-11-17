"""工具 schema。

纯数据，不含任何逻辑：改描述文案就在这里改，对模型可见的字段名一律不换。
"""
from mcp import types


def build_tools():
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
