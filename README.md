
# ZeroX: A Lightweight APR Agent via MCP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**ZeroX** (Zero Errors) is a lightweight Automated Program Repair (APR) agent based on the MCP (Model Context Protocol) and Nanobot framework. It is designed to solve the "Context Explosion" and infinite loop issues found in traditional APR agents (like RepairAgent) during long-running repair cycles.

By combining a minimalist toolchain, stateful external logging, and search-augmented localization, ZeroX achieves robust bug fixing with extreme token efficiency.

## 💡 Core Innovations (The "Agentless" Philosophy)

1. **Minimalist Toolchain & Stateful Logging**: By discarding massive native tool libraries, ZeroX keeps only 6 core MCP tools. The agent's reasoning process is forced into an external `work_log.json`, achieving extreme context compression. This stateful log also allows the LLM to seamlessly resume tasks. Moreover, every `run_test` call writes its outcome as a `test_result` field onto the latest work entry in `work_log.jsonl` (only the newest test result is kept, and `patch_and_log` clears it whenever a new round starts), so after a context compaction the agent can recover the most recent test result from `get_work_log` instead of wasting a turn on a meaningless test run.
2. **Atomic Patch Operations & Blocking Verification**: Through `patch_and_log`, the LLM is strictly forced to output `old/new` diffs rather than full code, guaranteeing atomicity with automatic backups. The `run_test` tool acts as a blocking function that automatically waits for execution to finish and returns parsed logs, eliminating useless polling.
3. **Emergent Self-Healing under Minimalist Tools**: Facing missing static metadata, the agent spontaneously exhibits advanced debugging behavior: autonomously injecting diagnostic probes into test/production code, writing runtime variables to temporary files, and cleaning them up. This proves that under a non-polluted context, a lightweight LLM possesses powerful emergent abilities. (Solidified into SOP in `AGENTS.md`).
4. **Nudge Theory & Asymmetric Memory Architecture**: Rollback means a clean slate for the AI (Preventing Context Poisoning), while all discarded patches and failed trajectories are strictly preserved in `human_audit.jsonl` for human researchers. This provides an untainted exploration space for the LLM while offering invaluable data for Error Analysis.

## 📊 Experimental Results (Defects4J Benchmark)

Tested on the first 4 bugs from the `Lang` project. **Total API cost: < $0.20** (Using DeepSeek API).

| Bug ID | Status | Time (s) | Tokens | Rounds | Breakthrough |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Lang_1b** | ✅ Fixed | 341.8s | 166K | 2 | Precisely located `NumberUtils` boundary condition. |
| **Lang_2b** | ✅ Fixed | 784.5s | 1.32M | 10 | **Masterpiece**: Faced missing logs, injected probes, encountered a compile error, executed a `rollback`, and finally fixed the locale bug. |
| **Lang_3b** | ✅ Fixed | 215.0s | 41K | 1 | Discovered unused variable and patched precision check logic. |
| **Lang_4b** | ✅ Fixed | 213.4s | 25K | 1 | Handled JSON parsing errors gracefully and fixed the logic. |

> **Total Repair Rate: 4/4 (100.0%)** 
> *(Note: The remaining 3 failing tests in Lang_2 are JDK environment noise (timezone/reflection) and unrelated to the core bug.)*

## 📁 Directory Structure

```text
ZeroX/
├── .gitignore              # Ignore venv, __pycache__, logs, backups, and real config
├── README.md               # Project documentation
├── AGENTS.md               # System prompts & Red Lines for the Agent (Core Asset)
├── config.example.json     # Template for configuration
├── zerox_server.py         # MCP Server source code
├── run_benchmark.py        # Automated batch testing script
├── logs/                   # Runtime logs, work logs, and benchmark metrics
└── backups/                # Automatic code backups per bug
```

## 🚀 Quick Start

**1. Requirements & Installation**
Ensure you have Python 3.10+ and an MCP-compatible client (like Claude Desktop, Cursor, or Nanobot).
```bash
pip install mcp openai
```

**2. Configuration**
Copy the template and fill in your absolute paths and LLM API keys:
```bash
cp config.example.json config.json
```
*(Note: Supports both Windows WSL and native Linux environments).*

**3. Run the MCP Server**
```bash
python zerox_server.py
```

**4. Automated Benchmark Testing**
To run the batch testing script (which spawns the MCP client and interacts with the LLM automatically):
```bash
python run_benchmark.py
```

## 🧩 Configuration Reference (Swap Test Suites Without Touching Code)

Everything environment-specific lives in `config.json`; the code only reads it. Four sections:

| Section | Purpose |
| :--- | :--- |
| `target` | Where the code lives and how to set it up / test it |
| `agent` | Behaviour knobs and prompts — the parts you tune often |
| `timeouts` | `test_execution` (server side) and `llm_client` (client side) |
| `llm_config` / `benchmark_bugs` | Model credentials and the bug list |

### `target` — workspace, commands and verdicts

| Field | Meaning |
| :--- | :--- |
| `tool_path` | Test framework / build tool entry point, exposed as `{tool}` |
| `workspace_root` | Root directory holding all bug workspaces, exposed as `{workspace_root}` |
| `workspace_template` | Per-bug directory template, exposed as `{workspace}` |
| `setup_cmd` | Run once per new bug (checkout/unzip/switch branch). Empty = nothing to set up |
| `test_cmd` | Run on every verification |
| `require_workspace` | Whether the incremental run checks that `{workspace}` exists first |
| `source_glob` | Default file pattern used by `search_code` |
| `bug_id_pattern` | Regex used to parse `bug_id`; **every named group becomes a placeholder** |
| `bug_id_template` | Canonical bug name (used for logs, backups and `work_log.jsonl`) |
| `default_version_suffix` | Appended to `{number}` when the pattern has no `suffix` group |
| `passed_pattern` | Regex hit ⇒ all tests pass ⇒ bug fixed |
| `failed_pattern` | Regex hit ⇒ tests fail; optional named groups `count` (int) and `detail` (stack trace) |

Available placeholders: `{tool}`, `{workspace_root}`, `{workspace}`, `{bug_id}`, plus every named group of `bug_id_pattern` (e.g. `{project}`, `{number}`, `{version}`, `{suffix}`). Workspace paths are shell-quoted automatically in `setup_cmd` / `test_cmd`.

**Example — an existing project, tested with Maven, no checkout step (works with a plain Windows-disk workspace under WSL):**

```json
"target": {
  "tool_path": "/usr/bin/mvn",
  "workspace_root": "/mnt/d/work/myproject",
  "workspace_template": "{workspace_root}",
  "setup_cmd": "",
  "test_cmd": "cd {workspace} && {tool} -B test",
  "require_workspace": false,
  "bug_id_pattern": "^(?P<bug_id>[A-Za-z0-9_\\-]+)$",
  "bug_id_template": "{bug_id}",
  "passed_pattern": "BUILD SUCCESS",
  "failed_pattern": "Tests run:.*?Failures: (?P<count>\\d+)"
}
```

**Example — one directory per bug, id doubles as the directory name (`quixbugs-quicksort` → `{workspace_root}/quixbugs-quicksort`):**

```json
"workspace_template": "{workspace_root}/{bug_id}",
"bug_id_pattern": "^(?P<bug_id>[A-Za-z0-9_\\-]+)$",
"bug_id_template": "{bug_id}",
"setup_cmd": "",
"test_cmd": "cd {workspace} && python run_tests.py 2>&1",
"passed_pattern": "All tests passed",
"failed_pattern": "FAILED \\((?P<count>\\d+)"
```

### `agent` — behaviour and prompts

| Key | Meaning |
| :--- | :--- |
| `max_attempts` | Max `patch_and_log` calls per bug (server side) |
| `max_turns` | Max LLM turns per bug (client side) |
| `system_prompt_file` | Prompt file, relative to the project root (absolute also works) |
| `user_prompt_template` | First user message; `{bug_id}` is substituted |
| `fixed_marker` / `failed_marker` | Strings the agent must output to end the task; keep them consistent with `AGENTS.md` |

---
*ZeroX is proudly built by a freshman at Guangdong University of Technology. (ZeroX 由广东工业大学大一新生独立开发)*
