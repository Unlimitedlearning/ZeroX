"""ZeroX Agent 入口：驱动 LLM 与 MCP Server 对话，直到测试报「全部通过」或预算用尽。

    python src/zerox.py                       # 跑 config.json 里的 benchmark_bugs
    python src/zerox.py --config case/config.json

本文件是 Agent 本体，不是「基准测试脚本」：循环、预算外账、指标都在这层
（服务端看不到 token，也看不到终态）。
"""
import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime
from openai import AsyncOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 同目录模块：事后审计（运行时不做编辑限制，跑完再判定 clean / invalid）
import audit
# 内部包在 src/zerox_core/：包名不能叫 zerox，否则会盖住本文件（src/zerox.py）
from zerox_core import PASSED_RESULT_MARKER
from zerox_core import config as config_mod

CONFIG_FILE = "config.json"

# 代码在 src/，而 config.json / logs/ / AGENTS.md 仍在项目根目录：
#   SRC_DIR      本文件所在目录（server 脚本在这里）
#   PROJECT_ROOT 项目根（配置、日志、提示词都在这里）
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)


def resolve_config_file():
    """选取配置文件：--config 优先，其次 ZEROX_CONFIG 环境变量，最后默认 config.json。

    相对路径按【项目根目录】解析（在哪个目录下敲 python 都一样）；选中的文件通过 ZEROX_CONFIG 传给 MCP Server，
    保证客户端和服务端读的是同一份配置。
    """
    chosen = None
    argv = sys.argv[1:]
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            chosen = argv[i + 1]
            break
        if arg.startswith("--config="):
            chosen = arg.split("=", 1)[1]
            break
    chosen = chosen or os.getenv("ZEROX_CONFIG") or CONFIG_FILE
    return chosen if os.path.isabs(chosen) else os.path.join(PROJECT_ROOT, chosen)


CONFIG_FILE = resolve_config_file()
# Server 子进程会继承这个变量，否则它会读回默认的 config.json
os.environ["ZEROX_CONFIG"] = CONFIG_FILE
SERVER_SCRIPT = os.path.join(SRC_DIR, "zerox_server.py")

LOG_DIR = os.path.join(PROJECT_ROOT, "logs")
# 基准指标由客户端写：token 用量只有调用 LLM 的这一端拿得到，服务端无法记录
METRICS_FILE = os.path.join(LOG_DIR, "benchmark_metrics.jsonl")

# 上下文压缩时保留的工具输出尾部长度（防止单条输出又把上下文撑爆）
COMPACT_TOOL_TAIL_CHARS = 6000
# 压缩后保留最近几轮完整对话，而不是只留一条工具输出：一刀切干净会让它把压缩前
# 刚读过的文件重新读一遍——Time_11 实测压缩后前三个调用就是在重读同一个测试方法。
COMPACT_KEEP_TURNS = 2
# 保留轮次里单条工具输出的字上限（留头留尾、中间注明原长）：否则一条 3MB 测试日志
# 能把刚压缩出来的上下文再撑爆一次。
COMPACT_KEEP_MSG_CHARS = 4000

# 上下文压缩的两个阈值都来自 config.json 的 agent 段落：
#   compact_context_chars  体量触发（窗口会不会爆）
#   compact_idle_reads     空转触发（有没有在原地读）
# 空转触发不再往工具返回里插提醒（实测模型无视，还污染结果）：「小步推进」写在
# AGENTS.md 里常驻。这里只留机制：连续 N 次只读（中间没有任何补丁/测试）就预约
# 两轮之后压缩一次，逼它醒来看 get_work_log。
# 数值在下面和 COMPACT_CONTEXT_CHARS 一起从 AGENT_CONF 读（这里还没读配置）。
FREE_TOOLS = {"search_code", "read_specific_code", "get_file_internal_structure", "get_work_log"}

# agent 段落的默认值在 src/zerox_core/config.py 的 DEFAULT_AGENT 里，与 zerox_server.py 共用同一份，
# 这里不留第二份（两份默认值迟早会漏改一份）。
# 注意：服务端只读 max_tests，客户端读剩下那几个；要改默认行为请改 config.json 的 agent 段落。


def resolve_path(path: str) -> str:
    """相对路径按项目根目录解析（配置/日志/提示词都在根目录），绝对路径原样返回。"""
    return path if os.path.isabs(path) else os.path.join(PROJECT_ROOT, path)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ 找不到配置文件 {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
LLM_CONF = CONFIG["llm_config"]
AGENT_CONF = config_mod.load_agent_config(CONFIG, CONFIG_FILE)
# 审计用的字段（test_globs）从 target 段落读，与 MCP Server 同一套默认值
TARGET_CONF = config_mod.load_target_config(CONFIG, CONFIG_FILE)
AUDIT_TEST_GLOBS = TARGET_CONF.get("audit_test_globs")

# 读取客户端超时
CLIENT_TIMEOUT = CONFIG.get("timeouts", {}).get("llm_client", 360.0)

# 兼容环境变量读取（如果你在 config 里写了 ${DEEPSEEK_API_KEY}）
api_key = LLM_CONF["api_key"]
if api_key.startswith("${") and api_key.endswith("}"):
    api_key = os.getenv(api_key[2:-1])

BUGS_TO_TEST = CONFIG.get("benchmark_bugs", ["Lang_1"])
# 轮次没有上限：收敛的硬闸门是服务端的 max_tests（见 run_single_bug 的循环）
# 上下文超过这么多字符就压缩（阈值写在 config.json 的 agent.compact_context_chars）
COMPACT_CONTEXT_CHARS = int(AGENT_CONF.get("compact_context_chars") or 160000)
# 连续只读调用到点就预约压缩（阈值在 agent.compact_idle_reads）；0 / null = 只按体量压缩。
# 必须放在 AGENT_CONF 之后：模块顶部那几行常量还没读配置，不能在那里取字段。
_idle_reads = AGENT_CONF.get("compact_idle_reads")
COMPACT_IDLE_READS = int(_idle_reads) if _idle_reads else None
FIXED_MARKER = AGENT_CONF["fixed_marker"]
FAILED_MARKER = AGENT_CONF["failed_marker"]
client = AsyncOpenAI(
    api_key=api_key, 
    base_url=LLM_CONF["base_url"],
    timeout=CLIENT_TIMEOUT  
)

with open(resolve_path(AGENT_CONF["system_prompt_file"]), "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()


def usage_to_counts(usage):
    """把 openai 的 usage 对象归一化成 (prompt, completion, total) 三个整数。"""
    if not usage:
        return 0, 0, 0
    prompt = getattr(usage, "prompt_tokens", None) or 0
    completion = getattr(usage, "completion_tokens", None) or 0
    total = getattr(usage, "total_tokens", None) or (prompt + completion)
    return prompt, completion, total



def append_metric(metric: dict):
    """把一条指标追加到 logs/benchmark_metrics.jsonl；写盘失败不影响批量测试。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(METRICS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(metric, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  ⚠️ 写入指标文件失败: {e}")


def canonical_bug_id(bug_id: str) -> str:
    """请求名 → 磁盘用的规范名（Lang_1 → Lang_1b）。

    服务端用 resolve_bug() 算工作区与 logs/<规范名>/，审计也按规范名找目录；
    客户端留档也走同一个纯函数，否则一次运行会裂成 Lang_1/（transcript）
    和 Lang_1b/（work_log、test.log）两个目录。
    """
    try:
        bug_ctx = config_mod.resolve_bug(TARGET_CONF, bug_id)
    except Exception:
        return bug_id
    return (bug_ctx or {}).get("bug_id") or bug_id


def transcript_path(bug_id: str) -> str:
    """每个 bug 一份人类可读的对话记录：logs/<规范名>/transcript.md。"""
    return os.path.join(LOG_DIR, canonical_bug_id(bug_id), "transcript.md")


def reset_transcript(bug_id: str):
    """每个 bug 开跑时重写一份（与服务端 checkout 时清空 work_log 同一时机）。"""
    path = transcript_path(bug_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {bug_id} agent transcript\n\n开始时间：{datetime.now().isoformat(timespec='seconds')}\n")
    except OSError as err:
        print(f"  ⚠️ [{bug_id}] 建 transcript 失败: {err}")


def append_transcript(bug_id: str, turn: int, tag: str, text: str, limit: int = 2000):
    """把一轮的 AI 输出 / 工具调用 / 工具结果追加进 transcript.md。

    只给人复盘用：压缩会把上下文里的历史丢掉，而这里留着一份可读流水。
    它【不】参与压缩判定 —— 只增不删的文件大小反映不了「当前上下文有多大」。
    """
    if not text:
        return
    body = text if len(text) <= limit else text[:limit] + f"\n…（截断，原长 {len(text)} 字）"
    try:
        with open(transcript_path(bug_id), "a", encoding="utf-8") as f:
            f.write(f"\n### turn {turn} · {datetime.now().strftime('%H:%M:%S')} · {tag}\n\n{body}\n")
    except OSError as err:
        print(f"  ⚠️ [{bug_id}] 写 transcript 失败: {err}")


def reasoning_text(msg) -> str:
    """取出模型自己吐的思考内容（不同 provider 字段名不同，取不到就返回空）。

    只写进 transcript.md 留档，【绝不能】回填进 messages：DeepSeek 的 reasoner 类模型
    明确禁止把 reasoning_content 再发回去（400）。所以它也不计入 context_chars。
    """
    extra = msg.get("model_extra") if isinstance(msg, dict) else getattr(msg, "model_extra", None)
    for name in ("reasoning_content", "reasoning"):
        value = msg.get(name) if isinstance(msg, dict) else getattr(msg, name, None)
        if not value and isinstance(extra, dict):
            value = extra.get(name)
        if value:
            return str(value).strip()
    return ""


def keep_recent_turns(messages, turns):
    """从尾部往前数 turns 个「assistant 消息 + 它的 tool 结果」组，返回可安全保留的切片。

    起点必须落在 assistant 消息上：tool 消息只能紧跟在带 tool_calls 的 assistant 后面，
    从一条 tool 消息中间切会直接 400。system 和压缩提示由调用方拼在前面。
    """
    starts = []
    for i, msg in enumerate(messages):
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
        if role == "assistant":
            starts.append(i)
    if not starts or turns <= 0:
        return []
    start = starts[-turns] if len(starts) >= turns else starts[0]
    return messages[start:]


def trim_tool_outputs(msgs, limit):
    """把保留轮次里的工具输出掐到 limit 字（留头留尾，中间标出原长）。

    只处理 role="tool" 的 dict：它们在客户端是我们自己拼的，可以安全复制；
    assistant 消息是 SDK 对象，原样传回给 API。
    """
    out = []
    for msg in msgs:
        if isinstance(msg, dict) and msg.get("role") == "tool":
            content = msg.get("content")
            if isinstance(content, str) and len(content) > limit:
                head = content[: limit * 2 // 3]
                msg = dict(msg, content=(
                    f"{head}\n…（截断，原长 {len(content)} 字）…\n"
                    f"{content[len(head):][-(limit - len(head)):]}"
                ))
        out.append(msg)
    return out


def context_chars(messages) -> int:
    """当前上下文里所有消息正文 + 工具参数的字符数（不是 token，也不是日志文件大小）。

    为什么不用 transcript.md 的大小当触发条件：那份文件只增不删，压缩之后它不会变小，
    一次压缩后就再也不会触发第二次，除非额外记一个「上次压缩时的 offset」。
    直接数 messages 没有这个问题，也不依赖磁盘。
    """
    total = 0
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(str(content))
        # tool_calls 的参数（patch 的 diff 之类）同样占上下文，不能漏
        calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
        for call in calls or []:
            fn = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
            if fn is None:
                continue
            args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", "")
            if args:
                total += len(str(args))
    return total


async def run_single_bug(bug_id: str, session: ClientSession):
    print(f"\n" + "="*50)
    print(f"🚀 开始测试 Bug: {bug_id}")
    print("="*50)
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": AGENT_CONF["user_prompt_template"].replace("{bug_id}", bug_id)}
    ]

    # 每个 bug 一份可读流水：AI 说了什么、调了什么、工具回了什么。
    # 压缩会丢掉上下文里的历史，但这份文件留着，复盘时不用去翻 LLM 的 window。
    reset_transcript(bug_id)
    
    mcp_tools = await session.list_tools()
    openai_tools = []
    for tool in mcp_tools.tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        })

    start_time = time.time()
    usage_totals = {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "turns": 0}
    # 补丁数/轮次/测试次数由客户端在循环里自己数（它就在现场），token 也只有它知道，
    # 所以服务端不重吐日志、客户端也不解析它的日志
    counters = {"patches": 0, "tests": 0, "round": 0}
    # 连续只读调用计数：patch_and_log / run_test / rollback 会把它清零
    free_streak = 0
    # 「到点强制压缩」的预约（0-based 的 turn 号）：只读 streak 到 COMPACT_IDLE_READS 时
    # 记下 turn+2 —— 下一轮留一轮反应时间，再下一轮顶部执行。写补丁/跑测试会取消它：
    # 已经在推进就不必擦记忆。这样「上下文将截断」才是可兑现的承诺，而不是随轮数漂移的口头警告。
    compact_due_turn = None
    # 终态只有三种：Fixed / Failed / Error，兜底值是 Error
    # 出口只有三个：「测试通过」「明确失败」「异常」
    final_status = "Error"
    # 【指标完整性】只在服务端真的报过「测试全部通过」后，才承认 agent 的 [FIXED]
    saw_passing_test = False
    # 本 bug 的第一次 run_test 显式声明全量 checkout（任务边界由客户端决定，见下方注入）
    fresh_task = True

    # 轮次没有上限：硬闸门是服务端的测试预算，用光时 run_test 返回【任务失败】把循环逼出去。
    # 只读轮次不消耗预算，所以纯只读的循环（一次测试都不跑）只能人工中断。
    turn = -1
    while True:
        turn += 1

        # 按体量触发压缩：读代码的轮次几乎不涨上下文，一次 3MB 的测试日志却能一把撑爆窗口，
        # 所以看的是上下文正文字数，不是轮数。阈值：config.json 的 agent.compact_context_chars
        ctx_chars = context_chars(messages)
        # 两个触发源走同一个入口：体量超阈值，或只读 streak 到点预约（见 COMPACT_IDLE_READS）。
        forced = compact_due_turn is not None and turn >= compact_due_turn
        if turn > 0 and (forced or ctx_chars >= COMPACT_CONTEXT_CHARS):
            reason = ("只读 streak 到点预约" if forced
                      else f"上下文已达 {ctx_chars} 字（阈值 {COMPACT_CONTEXT_CHARS}）")
            print(f"  ✂️ [{bug_id}] {reason}，触发压缩！清理历史冗余对话，逼迫 AI 读取工作日志...")
            
            # 保留 System Prompt
            system_prompt = messages[0]
            
            # 保留最近 N 轮完整对话（从 assistant 边界切，见 keep_recent_turns）：
            # 只留一条工具输出等于把刚形成的推理链一刀切断，它会重新读一遍。
            kept = trim_tool_outputs(keep_recent_turns(messages, COMPACT_KEEP_TURNS),
                                     COMPACT_KEEP_MSG_CHARS)

            # 找不到完整的最近轮次时（比如还没发生过 tool 调用），退回只留最后一条工具输出
            recent_tool_output = ""
            if not kept:
                for msg in reversed(messages):
                    # 兼容 dict 和 SDK 对象两种消息形态，否则永远匹配不到 tool 消息
                    role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
                    # 只保留最近的 tool 消息，最多保留 1 条
                    if role == "tool":
                        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                        recent_tool_output = content if isinstance(content, str) else str(content)
                        break
            
            reset_content = "SYSTEM NOTICE: The conversation history has been compacted to save memory. I have kept your most recent turns below. You should immediately call `get_work_log` to review your past attempts (each patch's reasoning and the test trend are there too), and then continue fixing."
            if recent_tool_output:
                # 只留尾部：测试报错的关键信息都在最后，也能避免单条输出再次撑爆上下文
                reset_content += "\n\n--- Most recent tool output (tail) ---\n" + recent_tool_output[-COMPACT_TOOL_TAIL_CHARS:]
            
            reset_prompt = {"role": "user", "content": reset_content}
            
            # 重新组装：System + 提示词(user) + 保留的最近几轮
            # 🚨 绝不能把 role="tool" 的消息单独留下：tool 消息必须紧跟在带 tool_calls 的 assistant 消息之后，
            #    否则 API 直接 400（Messages with role 'tool' must be a response to a preceding message with 'tool_calls'）
            messages = [system_prompt, reset_prompt] + kept
            append_transcript(bug_id, turn + 1, "compact",
                              f"上下文 {ctx_chars} 字 → {context_chars(messages)} 字，历史已丢弃；"
                              f"保留最近 {len(kept)} 条消息（≤{COMPACT_KEEP_TURNS} 轮，单条 ≤{COMPACT_KEEP_MSG_CHARS} 字）")
            # 注意：messages 末尾可能是 tool 消息（保留轮次带的），API 同样接受，模型会接着回复

            # 压缩就是「把当前记忆换掉」这个动作本身，所以两个计数器必须一起归零：
            # 否则 streak 还停在 12 以上，下一轮立刻又预约一次，变成无限反复擦记忆。
            compact_due_turn = None
            free_streak = 0

        try:
            response = await client.chat.completions.create(
                model=LLM_CONF["model_name"],
                messages=messages,
                tools=openai_tools,
                temperature=0.0
            )
            response_msg = response.choices[0].message

            messages.append(response_msg)
            
            # token 用量只有客户端拿得到：按 bug 累加，最后写进 benchmark_metrics.jsonl
            prompt_tokens, completion_tokens, turn_tokens = usage_to_counts(response.usage)
            usage_totals["prompt"] += prompt_tokens
            usage_totals["completion"] += completion_tokens
            usage_totals["total"] += turn_tokens
            usage_totals["calls"] += 1
            usage_totals["turns"] = turn + 1

            # 安全提取文本（解决了 UnboundLocalError）
            text = response_msg.content.strip() if response_msg.content else ""
            # 模型自己的思考过程（deepseek-flash 一般没有，reasoner 类才有）
            append_transcript(bug_id, turn + 1, "think", reasoning_text(response_msg), limit=4000)
            append_transcript(bug_id, turn + 1, "AI", text)

            # 如果 AI 输出了文本，简单打印并检查是否通关
            if text:
                print(f"  [{bug_id}] Turn {turn+1} AI: {text[:100]}...")
                if FIXED_MARKER in text:
                    # 自报 [FIXED] 不算数：得先有一次 run_test 真的返回了通过
                    if saw_passing_test:
                        final_status = "Fixed"
                    else:
                        print(f"  ⚠️ [{bug_id}] AI 自称 {FIXED_MARKER}，但从未见到通过的测试，记为 Failed。")
                        final_status = "Failed"
                    break
                if FAILED_MARKER in text:
                    final_status = "Failed"
                    break

            # 如果 AI 没调用工具，直接给系统警告，强制它继续
            if not response_msg.tool_calls:
                messages.append({
                    "role": "user", 
                    "content": "SYSTEM WARNING: Do NOT output natural language. You MUST call a tool immediately to proceed."
                })
                continue # 直接进入下一轮，不用打印乱七八糟的警告了

            for tool_call in response_msg.tool_calls:
                fn_name = tool_call.function.name
                print(f"  [{bug_id}] Turn {turn+1} Call: {fn_name}")
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    print(f"  ⚠️ [{bug_id}] 大模型输出了坏 JSON，已要求重试。Error: {e}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": fn_name,
                        "content": f"System Error: Invalid JSON arguments. {str(e)}. Please fix your JSON formatting (ensure all quotes are escaped) and try again."
                    })
                    continue # 直接进入下一轮对话，放弃执行本次工具

                try:
                    # 【单一策略源】服务端只负责执行与记账，不做任何调用去重 / 免费重跑判定；
                    # 客户端也不缓存结果——两边各持一套策略必然打架。
                    # 新任务的第一跑由客户端显式传 force_checkout：服务端从不自己决定「重置工作区」，
                    # 所以也不会在任务中途把已经打好的补丁冲掉。
                    if fn_name == "run_test" and fresh_task:
                        fn_args["force_checkout"] = True

                    # 留档写在注入之后：记录的是实际发出去的那份参数
                    append_transcript(bug_id, turn + 1, f"call:{fn_name}",
                                      json.dumps(fn_args, ensure_ascii=False))
                    result = await session.call_tool(fn_name, arguments=fn_args)
                    if fn_name == "run_test":
                        fresh_task = False
                    result_text = "".join([c.text for c in result.content if c.type == "text"])

                    if fn_name == "patch_and_log":
                        counters["patches"] += 1
                        called_round = fn_args.get("round")
                        if isinstance(called_round, int):
                            counters["round"] = max(counters["round"], called_round)
                    elif fn_name == "run_test":
                        counters["tests"] += 1
                    elif fn_name == "rollback" and not getattr(result, "isError", False):
                        # 回滚把那些轮次从日志里删掉了，客户端记的轮号也要跟着退，
                        # 否则 metrics 的 current_round 会比 work_log 里的实际轮号大（实测 4 vs 1）。
                        target_round = fn_args.get("target_round")
                        if isinstance(target_round, int):
                            counters["round"] = min(counters["round"], max(0, target_round - 1))

                    # 连续只读计次：到点就预约一次静默压缩（见 COMPACT_IDLE_READS）。
                    # 中途不插任何提示：文案改由 AGENTS.md 常驻说明，工具返回保持干净。
                    if fn_name in FREE_TOOLS:
                        free_streak += 1
                        if (COMPACT_IDLE_READS is not None
                                and free_streak >= COMPACT_IDLE_READS
                                and compact_due_turn is None):
                            compact_due_turn = turn + 2
                    else:
                        free_streak = 0
                        # 写了补丁 / 跑了测试 = 已经在推进，没必要再擦它的记忆
                        compact_due_turn = None

                    # 服务端只在 passed_pattern 命中后才会回这句话，是唯一的正样本信号
                    if fn_name == "run_test" and PASSED_RESULT_MARKER in result_text:
                        saw_passing_test = True

                    if "【任务失败】" in result_text:
                        print(f"  💀 [{bug_id}] Server 端测试预算用尽，任务结束。")
                        final_status = "Failed"
                        break
                except Exception as e:
                    result_text = f"Tool execution error: {str(e)}"
                    print(f"  ❌ Tool Error: {result_text}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": fn_name,
                    "content": result_text
                })

                # 工具返回同样是「AI 看到的东西」，一起留档（内部按 limit 截断）
                append_transcript(bug_id, turn + 1, f"result:{fn_name}", result_text)
            
            if final_status in ("Fixed", "Failed"):
                break

        except Exception as e:
            print(f"  ❌ [{bug_id}] 发生异常: {e}")
            final_status = "Error"
            break


    elapsed = time.time() - start_time
    print(f"  ⏱️ [{bug_id}] 结束。状态: {final_status}, 耗时: {elapsed:.2f}s, "
          f"Tokens: {usage_totals['total']} (prompt {usage_totals['prompt']} / completion {usage_totals['completion']})")
    return final_status, elapsed, usage_totals, counters

async def main():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[SERVER_SCRIPT],
        env=os.environ.copy()
    )

    print("🔥 正在启动 ZeroX MCP Server...")
    print(f"⚙️ 配置文件: {CONFIG_FILE}")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("✅ Server 已连接！开始批量测试...\n")
            
            results = {}
            total_start_time = time.time()
            
            for bug in BUGS_TO_TEST:
                # 【修复 Bug 2】接收 3 个返回值
                try:
                    status, elapsed, usage, counters = await run_single_bug(bug, session)
                except Exception:
                    # 批量跑一晚上，最怕「进程带着一份只存在于终端里的 traceback 直接死掉」。
                    # while 顶部（上下文计数 / 压缩判定）不在 run_single_bug 内部那层 try 里，
                    # 那里抛出就没人接，磁盘上只留一份半截 transcript。
                    # 落一份 crash 文件再上抛：终端会被滚没，文件不会。
                    crash_path = os.path.join(LOG_DIR, bug, "crash.txt")
                    try:
                        os.makedirs(os.path.dirname(crash_path), exist_ok=True)
                        with open(crash_path, "w", encoding="utf-8") as f:
                            f.write(traceback.format_exc())
                    except OSError:
                        pass
                    raise
                # 轮次/补丁数/测试次数由客户端在循环里自己数（它就在现场），token 也只有它知道
                rounds, patches = counters["round"], counters["patches"]
                # 事后审计：只读本地 work_log，几毫秒，不碰工作区也不跑测试
                try:
                    verdict = audit.audit_bug(LOG_DIR, bug, test_globs=AUDIT_TEST_GLOBS)
                except Exception as err:
                    verdict = {"audit": "unknown", "violations": [str(err)]}
                append_metric({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "bug_id": bug,
                    "bug_id_requested": bug,
                    "status": status,
                    "total_time_seconds": round(elapsed, 2),
                    "prompt_tokens": usage["prompt"],
                    "completion_tokens": usage["completion"],
                    "total_tokens": usage["total"],
                    "api_calls": usage["calls"],
                    "turns": usage["turns"],
                    "current_round": rounds,
                    "total_attempts": patches,
                    "tests": counters["tests"],
                    "audit": verdict.get("audit"),
                    # 判定规则是会变的东西：审计版本号写进每行指标，否则改了标准以后
                    # 同一份 benchmark_metrics.jsonl 里的结论分不清按哪版规则得出
                    "audit_version": getattr(audit, "AUDIT_VERSION", None),
                    "audit_violations": [v["path"] for v in verdict.get("violations") or []],
                    # 回滚掉的越界不算 invalid（最终状态是干净的），但一个 clean 到底是“没碰测试”
                    # 还是“碰了又回滚”完全是两回事，benchmark 必须能把它数出来。
                    "audit_rolled_back": [
                        v["path"] for v in verdict.get("rolled_back_violations") or []
                    ],
                })
                results[bug] = {
                    "status": status, 
                    "time": round(elapsed, 2),
                    "tokens": usage["total"],
                    "turns": usage["turns"],
                    "rounds": rounds,
                    "audit": verdict.get("audit"),
                }
                await asyncio.sleep(2)
                
            total_time = time.time() - total_start_time
            
            print("\n" + "="*55)
            print("📊 批量测试最终报告")
            print("="*55)
            print(f"{'Bug ID':<12} | {'Status':<10} | {'Time (s)':<10} | {'Tokens':<10} | {'Turns':<6} | {'Rounds':<6} | {'Audit':<8}")
            print("-" * 84)
            
            success_count = 0
            total_tokens = 0
            for bug, res in results.items():
                icon = "✅" if res["status"] == "Fixed" else "❌"
                print(f"{icon} {bug:<10} | {res['status']:<10} | {res['time']:<10} | {res['tokens']:<10} | {res['turns']:<6} | {res['rounds']:<6} | {res.get('audit') or '-':<8}")
                if res["status"] == "Fixed":
                    success_count += 1
                total_tokens += res["tokens"]
            
            print("-" * 84)
            total_bugs = len(BUGS_TO_TEST)
            if total_bugs > 0:
                print(f"总修复率: {success_count}/{total_bugs} ({(success_count/total_bugs)*100:.1f}%)")
            print(f"总 Token 消耗: {total_tokens}")
            print(f"总耗时: {total_time:.2f} 秒")
            print("="*55)
            
            print("\n💡 提示: 每个 Bug 的 token / 轮次 / 耗时详情已写入 logs/benchmark_metrics.jsonl")
            print("💡 事后审计: python src/audit.py（只读 work_log）/ python src/audit.py --verify（还原测试文件后复跑）")

if __name__ == "__main__":
    asyncio.run(main())
