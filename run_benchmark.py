import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from openai import AsyncOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CONFIG_FILE = "config.json"

# 无论从哪个目录启动，配置 / 提示词 / 服务端脚本都按本文件所在目录定位
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, CONFIG_FILE)
SERVER_SCRIPT = os.path.join(BASE_DIR, "zerox_server.py")

LOG_DIR = os.path.join(BASE_DIR, "logs")
# 基准指标由客户端写：token 用量只有调用 LLM 的这一端拿得到，服务端无法记录
METRICS_FILE = os.path.join(LOG_DIR, "benchmark_metrics.jsonl")

# 上下文压缩时保留的工具输出尾部长度（防止单条输出又把上下文撑爆）
COMPACT_TOOL_TAIL_CHARS = 6000

# agent 段落默认值：与 zerox_server.py 的 DEFAULT_AGENT 保持一致
DEFAULT_AGENT = {
    "max_attempts": 10,                      # 服务端每个 bug 的 patch 次数上限
    "max_turns": 100,                        # 客户端单个 bug 的最大对话轮数
    "system_prompt_file": "AGENTS.md",       # 相对本文件所在目录，也可以写绝对路径
    "user_prompt_template": "Please fix the bug for {bug_id}. Begin by calling run_test with bug_id='{bug_id}'.",
    "fixed_marker": "[FIXED]",               # agent 输出这个标记就算修复成功
    "failed_marker": "[FAILED]",
}


def load_agent_config(config):
    """把 config.json 的 agent 段落合并到默认值上；prompt 这类要经常微调的都在这里。"""
    agent_cfg = dict(DEFAULT_AGENT)
    user_agent = config.get("agent") or {}
    if not isinstance(user_agent, dict):
        print("❌ config.json 里的 'agent' 必须是一个对象")
        sys.exit(1)
    for key, value in user_agent.items():
        if value is not None:
            agent_cfg[key] = value
    return agent_cfg


def resolve_path(path: str) -> str:
    """相对路径按项目根目录解析，绝对路径原样返回。"""
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ 找不到配置文件 {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
LLM_CONF = CONFIG["llm_config"]
AGENT_CONF = load_agent_config(CONFIG)

# 读取客户端超时
CLIENT_TIMEOUT = CONFIG.get("timeouts", {}).get("llm_client", 360.0)

# 兼容环境变量读取（如果你在 config 里写了 ${DEEPSEEK_API_KEY}）
api_key = LLM_CONF["api_key"]
if api_key.startswith("${") and api_key.endswith("}"):
    api_key = os.getenv(api_key[2:-1])

BUGS_TO_TEST = CONFIG.get("benchmark_bugs", ["Lang_1"])
MAX_TURNS = AGENT_CONF["max_turns"]
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


def parse_work_log(text: str):
    """从 get_work_log 的返回文本里解析出 (轮次, 补丁数, 服务端解析出的 bug 名)。"""
    rounds = 0
    patches = 0
    resolved_bug = None

    match = re.match(r"\s*当前 Bug（(.+?)）", text)
    if match:
        resolved_bug = match.group(1)

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        patches += 1
        try:
            rounds = max(rounds, int(record.get("round") or 0))
        except (TypeError, ValueError):
            continue
    return rounds, patches, resolved_bug


async def fetch_work_log_stats(session, bug_id):
    """向服务端要一次工作日志，用它的轮次/补丁数填充指标；失败就退化成 0。"""
    try:
        result = await session.call_tool("get_work_log", arguments={"limit": 500})
        text = "".join([c.text for c in result.content if c.type == "text"])
    except Exception as e:
        print(f"  ⚠️ [{bug_id}] 读取工作日志失败，指标的轮次记为 0: {e}")
        return 0, 0, None
    return parse_work_log(text)


def append_metric(metric: dict):
    """把一条指标追加到 logs/benchmark_metrics.jsonl；写盘失败不影响批量测试。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(METRICS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(metric, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  ⚠️ 写入指标文件失败: {e}")

async def run_single_bug(bug_id: str, session: ClientSession):
    print(f"\n" + "="*50)
    print(f"🚀 开始测试 Bug: {bug_id}")
    print("="*50)
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": AGENT_CONF["user_prompt_template"].replace("{bug_id}", bug_id)}
    ]
    
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
    final_status = "Timeout/Failed"

    for turn in range(MAX_TURNS):

        if turn > 0 and turn % 20 == 0:
            print(f"  ✂️ [{bug_id}] 触发上下文压缩！清理历史冗余对话，逼迫 AI 读取工作日志...")
            
            # 保留 System Prompt
            system_prompt = messages[0]
            
            # 找到最近一次的工具返回（比如最后的报错信息）
            recent_tool_output = ""
            for msg in reversed(messages):
                # 兼容 dict 和 SDK 对象两种消息形态，否则永远匹配不到 tool 消息
                role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")
                # 只保留最近的 tool 消息，最多保留 1 条
                if role == "tool":
                    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                    recent_tool_output = content if isinstance(content, str) else str(content)
                    break
            
            reset_content = "SYSTEM NOTICE: The conversation history has been compacted to save memory. I have kept your most recent test result below. You should immediately call `get_work_log` to review your past attempts, and then continue fixing."
            if recent_tool_output:
                # 只留尾部：测试报错的关键信息都在最后，也能避免单条输出再次撑爆上下文
                reset_content += "\n\n--- Most recent tool output (tail) ---\n" + recent_tool_output[-COMPACT_TOOL_TAIL_CHARS:]
            
            reset_prompt = {"role": "user", "content": reset_content}
            
            # 重新组装：System + 提示词(user)，最近一次报错写进 user 内容里
            # 🚨 绝不能把 role="tool" 的消息单独留下：tool 消息必须紧跟在带 tool_calls 的 assistant 消息之后，
            #    否则 API 直接 400（Messages with role 'tool' must be a response to a preceding message with 'tool_calls'）
            messages = [system_prompt, reset_prompt]
            # 注意：因为现在 messages 最后一条是 user，符合 API 规范，AI 收到后必须回复（大概率会去调 get_work_log）

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

            # 如果 AI 输出了文本，简单打印并检查是否通关
            if text:
                print(f"  [{bug_id}] Turn {turn+1} AI: {text[:100]}...")
                if FIXED_MARKER in text:
                    final_status = "Fixed"
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

                # 删掉 try 外面那一行原生的 json.loads！！！
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
                    result = await session.call_tool(fn_name, arguments=fn_args)
                    result_text = "".join([c.text for c in result.content if c.type == "text"])
                    
                    if "【任务失败】" in result_text or "【系统强制熔断】" in result_text:
                        print(f"  💀 [{bug_id}] Server 端触发最大尝试次数上限。")
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
            
            if final_status in ("Fixed", "Failed"):
                break

        except Exception as e:
            print(f"  ❌ [{bug_id}] 发生异常: {e}")
            final_status = "Error"
            break


    elapsed = time.time() - start_time
    print(f"  ⏱️ [{bug_id}] 结束。状态: {final_status}, 耗时: {elapsed:.2f}s, "
          f"Tokens: {usage_totals['total']} (prompt {usage_totals['prompt']} / completion {usage_totals['completion']})")
    return final_status, elapsed, usage_totals

async def main():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[SERVER_SCRIPT],
        env=os.environ.copy()
    )

    print("🔥 正在启动 ZeroX MCP Server...")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            print("✅ Server 已连接！开始批量测试...\n")
            
            results = {}
            total_start_time = time.time()
            
            for bug in BUGS_TO_TEST:
                # 【修复 Bug 2】接收 3 个返回值
                status, elapsed, usage = await run_single_bug(bug, session)
                # 轮次/补丁数来自服务端 work_log（服务端唯一知道的状态），token 来自本轮统计
                rounds, patches, resolved_bug = await fetch_work_log_stats(session, bug)
                append_metric({
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "bug_id": resolved_bug or bug,
                    "bug_id_requested": bug,
                    "status": status,
                    "total_time_seconds": round(elapsed, 2),
                    "prompt_tokens": usage["prompt"],
                    "completion_tokens": usage["completion"],
                    "total_tokens": usage["total"],
                    "api_calls": usage["calls"],
                    "turns": usage["turns"],
                    "current_round": rounds,
                    "total_attempts": patches
                })
                results[bug] = {
                    "status": status, 
                    "time": round(elapsed, 2),
                    "tokens": usage["total"],
                    "turns": usage["turns"],
                    "rounds": rounds
                }
                await asyncio.sleep(2)
                
            total_time = time.time() - total_start_time
            
            print("\n" + "="*55)
            print("📊 批量测试最终报告")
            print("="*55)
            print(f"{'Bug ID':<12} | {'Status':<10} | {'Time (s)':<10} | {'Tokens':<10} | {'Turns':<6} | {'Rounds':<6}")
            print("-" * 72)
            
            success_count = 0
            total_tokens = 0
            for bug, res in results.items():
                icon = "✅" if res["status"] == "Fixed" else "❌"
                print(f"{icon} {bug:<10} | {res['status']:<10} | {res['time']:<10} | {res['tokens']:<10} | {res['turns']:<6} | {res['rounds']:<6}")
                if res["status"] == "Fixed":
                    success_count += 1
                total_tokens += res["tokens"]
            
            print("-" * 72)
            total_bugs = len(BUGS_TO_TEST)
            if total_bugs > 0:
                print(f"总修复率: {success_count}/{total_bugs} ({(success_count/total_bugs)*100:.1f}%)")
            print(f"总 Token 消耗: {total_tokens}")
            print(f"总耗时: {total_time:.2f} 秒")
            print("="*55)
            
            print("\n💡 提示: 每个 Bug 的 token / 轮次 / 耗时详情已写入 logs/benchmark_metrics.jsonl")

if __name__ == "__main__":
    asyncio.run(main())
