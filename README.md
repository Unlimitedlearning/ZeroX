
# ZeroX: A Lightweight APR Agent via MCP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**ZeroX** (Zero Errors) is a lightweight Automated Program Repair (APR) agent based on the MCP (Model Context Protocol) and Nanobot framework. It is designed to solve the "Context Explosion" and infinite loop issues found in traditional APR agents (like RepairAgent) during long-running repair cycles.

By combining a minimalist toolchain, stateful external logging, and search-augmented localization, ZeroX achieves robust bug fixing with extreme token efficiency.

## 💡 Core Innovations (The "Agentless" Philosophy)

1. **Minimalist Toolchain & Stateful Logging**: By discarding massive native tool libraries, ZeroX keeps only 6 core MCP tools. The agent's reasoning process is forced into an external `work_log.jsonl`, achieving extreme context compression, and this stateful log lets the LLM seamlessly resume a task. The log is an **append-only event stream** (`patch` / `test` / `note`) owned by a standalone `worklog.py`: test outcomes are never overwritten, so `get_work_log` can hand back a compact digest — what each round changed and whether the failure count is converging (`上轮 5 个 → 2 个`) — instead of replaying full `old/new` diffs. After a context compaction the agent recovers its diagnosis from that digest instead of wasting a turn on a meaningless test run.
2. **Atomic Patch Operations & Blocking Verification**: Through `patch_and_log`, the LLM is strictly forced to output `old/new` diffs rather than full code. Every write goes through `worklog.py`, which stores the before/after content as content-addressed blobs and applies a round atomically — if any file fails, all of them are restored. The `run_test` tool acts as a blocking function that automatically waits for execution to finish and returns parsed logs, eliminating useless polling.
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

### 📈 Metrics File

`logs/benchmark_metrics.jsonl` — one JSON line per finished bug, appended by `run_benchmark.py` after every run (including crashes and timeouts). The server cannot record this itself: token usage is only visible to the side that calls the LLM.

| Field | Meaning |
| :--- | :--- |
| `bug_id` | Bug name as resolved by the server (e.g. `Lang_2b`), with `bug_id_requested` holding your raw input (`Lang_2`) |
| `status` | One of `Fixed` / `Failed` / `Error` / `MaxTurns` — `Fixed` means `run_test` reported all tests passing and the agent stopped; `MaxTurns` means it ran out of `agent.max_turns` without converging |
| `total_time_seconds` | Wall-clock time for the whole bug |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | Token usage summed over every LLM call of that bug |
| `api_calls` / `turns` | Number of LLM requests / conversation turns consumed |
| `tests` / `total_attempts` / `current_round` | Test executions (the budget unit), patches applied, highest round reached |
| `audit` / `audit_violations` | Post-hoc verdict from `audit.py`: `clean` or `invalid`, plus the paths that make it invalid |
| `current_round` / `total_attempts` / `tests` | Highest round, applied patches and test executions, counted by the client inside its own tool loop |

## 📦 Benchmark Suite (BENCH30)

`config.example.json` ships a public 30-bug Defects4J subset — 8 projects, three difficulty tiers — intended as a cheap, repeatable sanity sweep before spending hours on the full benchmark. The `benchmark_bugs` array in that file is the canonical list; it is the only copy you need to reproduce a run.

### Reproduce

```bash
cp config.example.json config.json   # then set target.tool_path + target.workspace_root
export DEEPSEEK_API_KEY=...
python run_benchmark.py
```

Everything machine-specific lives in `config.json` (`tool_path`, `workspace_root`, credentials) and is git-ignored; nothing else needs editing. `run_benchmark.py` calls the model with `temperature=0`, so runs are near-deterministic — residual variation comes from provider-side batching, which is why the reporting section below still asks for repeats.

Built and verified against **Defects4J 1.5.0**, whose bug-id ranges are Lang 1–65, Math 1–106, Chart 1–26, Time 1–27, Compress 1–47, Codec 1–18, Csv 1–16, Cli 1–40. Lang 18/25/48, Time 21 and Cli 6 do not exist upstream and are never referenced.

### The 30 bugs

| Tier | Intent | Bugs |
| :--- | :--- | :--- |
| **A — floor probes** (8) | single-class, 1–5 line fixes. If this tier does not go ≥6/8, the toolchain or the prompt is broken — not the bug | `Lang_1` `Lang_4` `Lang_10` `Csv_1` `Codec_5` `Codec_11` `Cli_5` `Cli_13` |
| **B — main battleground** (14) | needs cross-method tracing or an invariant; the real repair rate shows up here (30–70% is a healthy spread) | `Lang_43` `Lang_51` `Lang_60` `Chart_1` `Chart_13` `Chart_26` `Time_4` `Time_11` `Time_19` `Csv_9` `Compress_7` `Compress_25` `Compress_38` `Cli_28` |
| **C — stress** (8) | numerical semantics and binary formats; 0–2/8 is a normal outcome | `Math_2` `Math_30` `Math_63` `Math_70` `Math_73` `Math_95` `Time_14` `Compress_45` |

Tiering is a **prior** based on project and patch size, not ground truth. Calibrate it against the metadata before trusting it (`defects4j` on `PATH`):

```bash
defects4j info -p Lang -b 1     # modified classes / methods
defects4j bids -p Time         # bug ids that actually exist
```

### Deliberately excluded

Closure, JacksonDatabind, Mockito and Gson. Single-bug test time (Closure runs 5–10 minutes), JDK sensitivity (Mockito) and very large suites turn a 30-bug sweep from ~3 hours into 20+, while injecting environment noise into the sample. They belong to a second phase.

### What to report

1. `logs/benchmark_metrics.jsonl` — one line per finished bug (`status`, tokens, rounds, wall time).
2. **plausible vs correct** — "all tests pass" is a plausible patch, not a correct one. Cross-check against the developer patch (`revision.id.fixed` in `defects4j/framework/projects/<Project>/active-bugs.csv`), and run `python audit.py --verify` to replay every finished bug with its test files restored: that re-run is what `Verified Fixed` means.
3. **variance** — LLMs are non-deterministic; repeat each bug ≥3 times and report mean ± std.
4. **per-tier and per-project rates** — one aggregate number hides everything interesting.

`logs/benchmark_metrics.jsonl`, `logs/audit.jsonl` and `logs/<bug>/human_audit.jsonl` are the only files under `logs/` that git tracks, so published numbers stay auditable without committing every scratch log. If tier C suddenly scores far above the literature, run `python audit.py` and read the `invalid` rows before celebrating.

## 📁 Directory Structure

```text
ZeroX/
├── .gitignore              # Ignore venv, __pycache__, logs and real config
├── README.md               # Project documentation
├── AGENTS.md               # System prompt: tools, budget, workflow (Core Asset)
├── config.example.json     # Template for configuration
├── zerox_server.py         # MCP Server source code
├── worklog.py              # Standalone version-control module (blobs + append-only events + rollback)
├── audit.py                # Post-hoc audit: Fixed vs Verified Fixed, no runtime edit restrictions
├── run_benchmark.py        # Automated batch testing script
├── case/                   # Single-file practice case (List_999.java + pristine copy)
├── config.list999.json     # Alternate config for the bundled Java case
├── tools/                  # Offline selftests (worklog, audit) + new_run.py (archive last batch)
├── logs/                   # Runtime logs, work logs, blob store, and benchmark metrics
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
To benchmark the bundled single-file case instead of Defects4J, point the script at its config (`--config` is forwarded to the server via `ZEROX_CONFIG`, so both sides read the same file):
```bash
python run_benchmark.py --config config.list999.json
```

Before each new batch, archive the previous batch instead of letting the numbers mix — `python tools/new_run.py` moves everything under `logs/` into `logs/archive/<timestamp>/` (keeping a `manifest.json`), clears `__pycache__`, and prints the bug list it is about to run. The archived batch stays auditable: `python audit.py --logs logs/archive/<timestamp>`.

**5. Bundled Single-File Case (`case/List_999.java`)**
A self-testing Java program that exercises the same toolchain without Defects4J:

- `case/List_999.java` — a `SortedInts` container (binary-search bounds, counting, ranged copy, bulk removal, sorted insert, nearest lookup) plus a harness in `main`. It prints one `[PASS]` / `[FAIL]` line per case and ends with `All tests passed` or `FAILED (n)`.
- Baseline is `7/10`: the three duplicate-related cases (`count duplicates`, `last index of duplicates`, `remove all duplicates`) fail, everything else passes — one root cause, several delegated symptoms.
- No comments anywhere, so the only ground truth is the test output.
- Each case runs on a daemon thread with a 3s join timeout, so an exception or an infinite loop only fails that case.
- `case/List_999.pristine.bak` — untouched snapshot; `target.setup_cmd` copies it back before every run. Keep the two files identical: `cp case/List_999.java case/List_999.pristine.bak`.

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
| `setup_cmd` | Run once per new bug (checkout/unzip/switch branch). Empty = nothing to set up. A fresh checkout also clears that bug's `work_log.jsonl`, because the workspace is back to its initial state and old rounds no longer apply |
| `test_cmd` | Run on every verification |
| `require_workspace` | Whether the incremental run checks that `{workspace}` exists first |
| `source_glob` | Default file pattern used by `search_code` |
| `bug_id_pattern` | Regex used to parse `bug_id`; **every named group becomes a placeholder** |
| `bug_id_template` | Canonical bug name (used for logs and `work_log.jsonl`) |
| `default_version_suffix` | Appended to `{number}` when the pattern has no `suffix` group |
| `passed_pattern` | Regex hit ⇒ all tests pass ⇒ bug fixed |
| `failed_pattern` | Regex hit ⇒ tests fail; optional named groups `count` (int) and `detail` (stack trace) |
| `patch_allow_globs` | Optional whitelist (`fnmatch`). **Empty by default** = any path inside the workspace is patchable |
| `patch_deny_globs` | Optional blacklist. Hit ⇒ rejected, takes priority over the whitelist. Empty by default |
| `patch_must_be_under_workspace` | Reject any patch whose path is outside `{workspace}` (default `true`). Relative paths are resolved against `{workspace}` before the check |
| `audit_test_globs` | Which paths count as test code — **read only by `audit.py`, after the run** |

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

### No runtime edit restrictions — audit after the fact

ZeroX deliberately does **not** police which files the agent edits while it works. The single runtime rule is "stay inside `{workspace}`": `patch_and_log` rejects `..` traversal and paths outside the workspace, and resolves relative paths against it first (`src/main/java/...` works).

Earlier versions had two guards — a `src/main/java` whitelist plus an `md5` baseline of the test tree re-checked before every run. Both were removed on purpose:

- the whitelist rejected legitimate patches. Models routinely pass `src/main/java/...` instead of an absolute path, and every rejection pushed the agent into firing a random patch to regain permission — at 3-6 minutes a test;
- the baseline needed four extra config fields (`test_guard_suffixes`, `test_guard_ignore_dirs`, ...) just to avoid flagging Defects4J build output.

The judgement moved to `audit.py`, which reads the finished `work_log.jsonl` and reports two separate labels:

| Label | Meaning |
| :--- | :--- |
| `audit: clean` / `invalid` | `invalid` = at least one round patched a file matching `target.audit_test_globs` |
| `verified: true` / `false` | `python audit.py --verify` restores those test files (`git checkout --`, or `rm -f` for files the agent created), re-runs `test_cmd`, and checks `passed_pattern` again — passing with the suite restored means the fix does not lean on the edited tests |

Because the verdict is derived from the append-only log, it can be replayed over historical runs months later, without touching the workspace or spending a single test. Publish both numbers: **Fixed** and **Verified Fixed**.

**No call arbitration, no free-rerun accounting.** Both used to live in the server and both were removed deliberately: they policed the cheapest actions (a few seconds of reading, one test) while pushing the agent into random patches just to regain permission to read — and a random patch costs a whole 3-6 minute test. What is left is a single hard budget, **test executions per bug** (`agent.max_tests`, default 12), plus one line of `[预算]` bookkeeping appended to every `run_test` / `patch_and_log` reply. Patches are unlimited and free; reading is unlimited and free.

**`run_test` never resets the workspace on its own.** A checkout happens only when the caller explicitly passes `force_checkout: true` (the benchmark client does this on the first call of each bug) or when the bug id changes with no patches pending. Switching bugs while patches are pending is refused rather than silently wiping them — the old rule (`reset_task_state` flipping `IS_TASK_INITIALIZED` triggered a re-checkout) was what made the agent look *stuck at `run_test`*: every retry re-ran on freshly checked-out, still-buggy code.

> **Known blind spot:** the single-file case (`config.list999.json`) keeps the code under test and the harness in the same file, so `audit_test_globs` cannot tell them apart — it is set to `[]` there, every patch audits as `clean`, and the prompt is the only constraint.

### `agent` — behaviour and prompts

| Key | Meaning |
| :--- | :--- |
| `max_tests` | Max `run_test` executions per bug (server side); the only hard budget |
| `max_turns` | Max LLM turns per bug (client side) |
| `system_prompt_file` | Prompt file, relative to the project root (absolute also works) |
| `user_prompt_template` | First user message; `{bug_id}` is substituted |
| `fixed_marker` / `failed_marker` | Strings the agent must output to end the task; keep them consistent with `AGENTS.md` |

---
*ZeroX is proudly built by a freshman at Guangdong University of Technology. (ZeroX 由广东工业大学大一新生独立开发)*
