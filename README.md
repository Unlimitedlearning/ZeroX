
# ZeroX: A Lightweight APR Agent via MCP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**ZeroX** (Zero Errors) is a lightweight Automated Program Repair (APR) agent based on the MCP (Model Context Protocol) and Nanobot framework. It is designed to solve the "Context Explosion" and infinite loop issues found in traditional APR agents (like RepairAgent) during long-running repair cycles.

## 💡 Core Innovations (The "Agentless" Philosophy)

1. **Minimalist Toolchain & Stateful Logging**: By discarding complex AST parsing and massive native tool libraries, ZeroX keeps only 6 core MCP tools. The agent's reasoning process is forced into an external `work_log.json`, achieving extreme context compression. This stateful log also allows the LLM to seamlessly resume tasks in a new conversation.
2. **Atomic Patch Operations**: Through the `patch_and_log` tool, the LLM is strictly forced to output `old/new` code diffs instead of full code. This guarantees atomicity: automatic backups before writing, and automatic rollback upon failure.
3. **Emergent Self-Healing under Minimalist Tools**: 
   Under the constraint of only 6 basic tools, when facing missing static metadata in Defects4J, the agent spontaneously exhibited advanced debugging behavior: autonomously injecting diagnostic probes into test/production code, writing runtime variables to temporary files, and actively cleaning them up after getting the ground truth. This proves that under a non-polluted context, a lightweight LLM possesses emergent abilities to break through information blind spots. (This emergent behavior has been solidified into an SOP in `AGENTS.md`).
4. **Nudge Theory & Asymmetric Memory Architecture**: 
   We separate the memory for AI and humans. For the AI, rollback means a clean slate (Preventing Context Poisoning and "Learned Helplessness"). For human developers, all discarded patches and failed trajectories are strictly preserved in `rollback_history.jsonl`. This provides an untainted exploration space for the LLM while offering researchers invaluable data for Prompt optimization and Error Analysis.

## 📊 Experimental Results (Defects4J Benchmark)

Tested on 3 real bugs from the `Lang` project. **Total API cost: ~$0.30** (Using DeepSeek API).

| Bug ID | Status | Rounds | Issue Type | Breakthrough |
| :--- | :--- | :--- | :--- | :--- |
| **Lang_1b** | ✅ Fixed | 1 | Hex overflow | Precisely located `NumberUtils` boundary condition. |
| **Lang_2b** | ✅ Fixed | 7 | JDK 11 CLDR | **Masterpiece**: Faced missing logs, injected probes, encountered a compile error, rolled back, and finally fixed the `#` suffix locale bug in round 7. |
| **Lang_3b** | ✅ Fixed | 1 | Float precision | Discovered unused `numDecimals` variable and patched precision check logic. |

*(Note: The remaining 3 failing tests in Lang_2 are JDK environment noise (timezone/reflection) and unrelated to the core bug.)*

## 📁 Directory Structure

```text
ZeroX/
├── .gitignore            # Ignore venv, __pycache__, logs, backups, and real config
├── README.md             # Project documentation
├── AGENTS.md             # System prompts for the Agent (Core Asset)
├── config.example.json   # Template for configuration
├── zerox_server.py       # MCP Server source code
├── logs/                 # Runtime logs and work logs
└── backups/              # Automatic code backups per bug
```

## 🚀 Quick Start

1. Clone the repository and install dependencies: `pip install mcp`
2. Copy `config.example.json` to `config.json` and fill in your local absolute paths (supporting both Windows WSL and native Linux).
3. Start the MCP server: `python zerox_server.py`
4. Connect it to your Nanobot (or any MCP-compatible client) and start repairing!

```
ZeroX is proudly built by a freshman at Guangdong University of Technology. (ZeroX 由广东工业大学大一新生独立开发)
```