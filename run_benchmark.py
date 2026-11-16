import asyncio
import json
import os
import sys
import time
from openai import AsyncOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CONFIG_FILE = "config.json"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ 找不到配置文件 {CONFIG_FILE}")
        sys.exit(1)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()
LLM_CONF = CONFIG["llm_config"]

# 兼容环境变量读取（如果你在 config 里写了 ${DEEPSEEK_API_KEY}）
api_key = LLM_CONF["api_key"]
if api_key.startswith("${") and api_key.endswith("}"):
    api_key = os.getenv(api_key[2:-1])

BUGS_TO_TEST = CONFIG.get("benchmark_bugs", ["Lang_1"])
MAX_TURNS = 20

client = AsyncOpenAI(api_key=api_key, base_url=LLM_CONF["base_url"])

with open("AGENTS.md", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

async def run_single_bug(bug_id: str, session: ClientSession):
    print(f"\n" + "="*50)
    print(f"🚀 开始测试 Bug: {bug_id}")
    print("="*50)
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Please fix the bug for {bug_id}. Begin by calling run_defects4j_test."}
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
    total_tokens_used = 0
    final_status = "Timeout/Failed"

    for turn in range(MAX_TURNS):
        try:
            response = await client.chat.completions.create(
                model=LLM_CONF["model_name"],
                messages=messages,
                tools=openai_tools,
                temperature=0.0
            )
            response_msg = response.choices[0].message
            # 注意：response_msg 是一个 Pydantic 对象，直接 append 进去某些 API 会报错，通常需要用 .model_dump() 或作为 dict 处理
            # 但在 OpenAI SDK 里直接 append 是支持的，保持你的写法。
            messages.append(response_msg)
            
            if response.usage:
                total_tokens_used += response.usage.total_tokens

            if response_msg.content:
                text = response_msg.content.strip()
                print(f"  [{bug_id}] Turn {turn+1} AI: {text[:100]}...")
                
                if "[FIXED]" in text:
                    print(f"  🎉 [{bug_id}] 修复成功！")
                    final_status = "Fixed"
                    break
                if "[FAILED]" in text:
                    print(f"  💀 [{bug_id}] AI 主动放弃或触发熔断。")
                    final_status = "Failed"
                    break

            if not response_msg.tool_calls:
                print(f"  ⚠️ [{bug_id}] AI 没有调用工具，也没有输出结束符。")
                messages.append({"role": "user", "content": "Please use tools or output [FIXED]/[FAILED]."})
                continue

            for tool_call in response_msg.tool_calls:
                fn_name = tool_call.function.name
                fn_args = json.loads(tool_call.function.arguments)
                print(f"  [{bug_id}] Turn {turn+1} Call: {fn_name}")
                
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

    # 【修复 Bug 1】return 必须放在 for 循环外面，否则只跑第一轮就退出
    elapsed = time.time() - start_time
    print(f"  ⏱️ [{bug_id}] 结束。状态: {final_status}, 耗时: {elapsed:.2f}s, Tokens: {total_tokens_used}")
    return final_status, elapsed, total_tokens_used

async def main():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["zerox_server.py"],
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
                status, elapsed, tokens = await run_single_bug(bug, session)
                results[bug] = {
                    "status": status, 
                    "time": round(elapsed, 2),
                    "tokens": tokens
                }
                await asyncio.sleep(2)
                
            total_time = time.time() - total_start_time
            
            print("\n" + "="*55)
            print("📊 批量测试最终报告")
            print("="*55)
            print(f"{'Bug ID':<12} | {'Status':<10} | {'Time (s)':<10} | {'Tokens':<10}")
            print("-" * 55)
            
            success_count = 0
            total_tokens = 0
            for bug, res in results.items():
                icon = "✅" if res["status"] == "Fixed" else "❌"
                print(f"{icon} {bug:<10} | {res['status']:<10} | {res['time']:<10} | {res['tokens']:<10}")
                if res["status"] == "Fixed":
                    success_count += 1
                total_tokens += res["tokens"]
            
            print("-" * 55)
            total_bugs = len(BUGS_TO_TEST)
            if total_bugs > 0:
                print(f"总修复率: {success_count}/{total_bugs} ({(success_count/total_bugs)*100:.1f}%)")
            print(f"总 Token 消耗: {total_tokens}")
            print(f"总耗时: {total_time:.2f} 秒")
            print("="*55)
            
            print("\n💡 提示: 详细 Token 消耗与轮次数据，请查看 logs/benchmark_metrics.jsonl")

if __name__ == "__main__":
    asyncio.run(main())