
# ZeroX: A Lightweight APR Agent via MCP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**ZeroX** (Zero Errors) is a lightweight Automated Program Repair (APR) agent based on the MCP (Model Context Protocol) and Nanobot framework. It is designed to solve the "Context Explosion" and infinite loop issues found in traditional APR agents (like RepairAgent) during long-running repair cycles.

By combining a minimalist toolchain, stateful external logging, and search-augmented localization, ZeroX achieves robust bug fixing with extreme token efficiency.

## 💡 Core Innovations (The "Agentless" Philosophy)

1. **Minimalist Toolchain & Stateful Logging**: By discarding massive native tool libraries, ZeroX keeps only 7 core MCP tools. The agent's reasoning process is forced into an external `work_log.jsonl`, achieving extreme context compression, and this stateful log lets the LLM seamlessly resume a task. The log is an **append-only event stream** (`patch` / `test` / `note`) owned by a standalone `worklog.py` (the single rewrite is `rollback()`, which archives the dropped events to `human_audit.jsonl` first): test outcomes are never overwritten, so `get_work_log` can hand back a compact digest — what each round changed and whether the failure count is converging (`上轮 5 个 → 2 个`) — instead of replaying full `old/new` diffs. After a context compaction the agent recovers its diagnosis from that digest instead of wasting a turn on a meaningless test run.
2. **Atomic Patch Operations & Blocking Verification**: Through `patch_and_log`, the LLM is strictly forced to output `old/new` diffs rather than full code. Every write goes through `worklog.py`, which stores the before/after content as content-addressed blobs and applies a round atomically — if any file fails, all of them are restored. The `run_test` tool acts as a blocking function that automatically waits for execution to finish and returns parsed logs, eliminating useless polling.
3. **Emergent Self-Healing under Minimalist Tools**: Facing missing static metadata, the agent spontaneously exhibits advanced debugging behavior: autonomously injecting diagnostic probes into test/production code, writing runtime variables to temporary files, and cleaning them up. This proves that under a non-polluted context, a lightweight LLM possesses powerful emergent abilities. (Solidified into SOP in `AGENTS.md`).
4. **Nudge Theory & Asymmetric Memory Architecture**: Rollback means a clean slate for the AI (Preventing Context Poisoning), while all discarded patches and failed trajectories are strictly preserved in `human_audit.jsonl` for human researchers. This provides an untainted exploration space for the LLM while offering invaluable data for Error Analysis.

## 📊 Experimental Results (Defects4J Benchmark)

Environment: **Defects4J 3.0.1** (`v3.0.1-7-g8c16da82`), Java 11, workspace on ext4, DeepSeek `deepseek-flash` at `temperature=0`. Every row comes from `benchmark_metrics.jsonl`; `Audit` is `python src/audit.py`, `Verified` is `python src/audit.py --verify`. Batch `logs/archive/20261118-133109` holds the setup-verification run, the current batch holds the rest.

| Bug ID | Status | Time (s) | Tokens | In / Out | Turns | Patch calls | Tests | Audit | Verified |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Chart_1b** | ✅ Fixed | 34.75 | 46.8K | 45,365 / 1,392 | 11 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Lang_43b** | ✅ Fixed | 82.35 | 57.4K | 51,433 / 5,965 | 8 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Lang_51b** | ✅ Fixed | 34.82 | 27.0K | 25,917 / 1,103 | 7 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Lang_60b** | ✅ Fixed | 41.98 | 37.9K | 36,565 / 1,329 | 10 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Time_4b** | ✅ Fixed | 83.78 | 112.1K | 100,745 / 11,318 | 10 | 2 | 2 | `clean` | ✅ `Verified Fixed` |

> **5/5 Fixed · 5/5 `clean` · 5/5 `Verified Fixed`.** Every bug converged in one round and two test executions — the 12-test budget was never approached, and wall time is 35–84s. `Verified Fixed` means `audit.py --verify` restored the test files, re-ran the suite and it passed again, i.e. no fix leans on an edited test.
>
> `Patch calls` counts `patch_and_log` **requests**, which is what the client sees. `Time_4` needed two: its first call never landed (the server logged `累计 1 次修改` and `work_log.jsonl` holds a single `patch` event), so one call was rejected and retried. The audit's own `补丁 N` is the applied count — report both if the distinction matters.
>
> Coverage caveat before this goes anywhere near a paper: all five bugs build with **ant** (`Lang`, `Chart`, `Time`). The maven chain (`Csv`, `Compress`) has not been exercised yet, and 5/5 at tier-B difficulty is a small sample — repeat before quoting a rate.
>
> The same `Chart_1` took **658s** / 30.7K tokens / 9 turns when its workspace sat on a Windows drive (`/mnt/d`), with the same `Fixed` + `clean` verdict: wall time follows the filesystem (19×), token and turn counts follow the trajectory — only `Status` and `Audit` are stable across environments.

### 📈 Metrics File

`logs/benchmark_metrics.jsonl` — one JSON line per finished bug, appended by `src/zerox.py` (the agent) after every run (including crashes and timeouts). The server cannot record this itself: token usage is only visible to the side that calls the LLM.

| Field | Meaning |
| :--- | :--- |
| `bug_id` / `bug_id_requested` | Both hold the id you put in `benchmark_bugs` (e.g. `Lang_1`). The log directory on disk uses the canonical name (`Lang_1b`), so `audit.py` maps `Lang_1` → `Lang_1b` itself |
| `status` | One of `Fixed` / `Failed` / `Error` / `MaxTurns` — `Fixed` means `run_test` reported all tests passing and the agent stopped; `MaxTurns` means it ran out of `agent.max_turns` without converging |
| `total_time_seconds` | Wall-clock time for the whole bug |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | Token usage summed over every LLM call of that bug |
| `api_calls` / `turns` | Number of LLM requests / conversation turns consumed |
| `tests` / `total_attempts` / `current_round` | Test executions (the budget unit), `patch_and_log` **calls** (a rejected call still counts — see `Time_4` above), highest round reached |
| `audit` / `audit_violations` | Post-hoc verdict from `audit.py`: `clean` or `invalid`, plus the paths that make it invalid |
| `audit_version` | Version of the audit rules that produced that verdict (`audit.AUDIT_VERSION`) — bump it when the rules change, so older rows stay identifiable in the same file |

## 📦 Benchmark Suite (BENCH30)

`config.example.json` ships a public 30-bug Defects4J subset — 8 projects, three difficulty tiers — intended as a cheap, repeatable sanity sweep before spending hours on the full benchmark. The `benchmark_bugs` array in that file is the canonical list; it is the only copy you need to reproduce a run.

### Reproduce

```bash
cp config.example.json config.json   # then set target.tool_path + target.workspace_root
export DEEPSEEK_API_KEY=...
python src/zerox.py
```

Everything machine-specific lives in `config.json` (`tool_path`, `workspace_root`, credentials) and is git-ignored; nothing else needs editing. `src/zerox.py` calls the model with `temperature=0`, so runs are near-deterministic — residual variation comes from provider-side batching, which is why the reporting section below still asks for repeats.

Built and verified against **Defects4J 3.0.1** (`v3.0.1-7-g8c16da82`, Java 11 required), whose bug-id ranges are Lang 1–65, Math 1–106, Chart 1–26, Time 1–27, Compress 1–47, Codec 1–18, Csv 1–16, Cli 1–40. Lang 18/25/48, Time 21 and Cli 6 do not exist upstream and are never referenced.

Setting it up takes ~10 minutes (`init.sh` pulls a 600 MB project bundle plus the Major / EvoSuite / Randoop jars). One manual step is needed on any machine: the `commons-csv` repository inside the official bundle ships CRLF blobs, so Defects4J's own `git apply` fails on `Csv_*` checkouts unless whitespace checking is off —

```bash
git config --global apply.ignoreWhitespace change
```

ZeroX itself is unaffected (the server normalizes `\r\n` before diffing and before matching `old_str`), but Defects4J cannot check out `Csv_*` without that setting.

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

Closure, JacksonDatabind, Mockito and Gson. Single-bug test time (Closure takes minutes per test even on ext4), JDK sensitivity (Mockito) and very large suites would dominate a 30-bug sweep while injecting environment noise into the sample. They belong to a second phase.

### What to report

1. `logs/benchmark_metrics.jsonl` — one line per finished bug (`status`, tokens, rounds, wall time).
2. **plausible vs correct** — "all tests pass" is a plausible patch, not a correct one. Cross-check against the developer patch (`revision.id.fixed` in `defects4j/framework/projects/<Project>/active-bugs.csv`), and run `python src/audit.py --verify` to replay every finished bug with its test files restored: that re-run is what `Verified Fixed` means.
3. **variance** — LLMs are non-deterministic; repeat each bug ≥3 times and report mean ± std.
4. **per-tier and per-project rates** — one aggregate number hides everything interesting.

`logs/benchmark_metrics.jsonl`, `logs/audit.jsonl` and `logs/<bug>/human_audit.jsonl` are the only files under `logs/` that git tracks, so published numbers stay auditable without committing every scratch log. If tier C suddenly scores far above the literature, run `python src/audit.py` and read the `invalid` rows before celebrating.

## 📁 Directory Structure

```text
ZeroX/
├── .gitignore              # Ignore venv, __pycache__, logs, the real config and one-off tmp scripts
├── README.md               # Project documentation
├── AGENTS.md               # System prompt: tools, budget, workflow (Core Asset)
├── config.example.json     # Template for configuration
├── src/                    # All ZeroX code
├── src/zerox.py            # Agent entry point: LLM loop, budget accounting, metrics (the only writer)
├── src/zerox_server.py     # MCP Server: the 7 tools that touch the workspace
├── src/zerox_core/         # Package: config defaults + validation, tool schemas, command executor
├── src/worklog.py          # Standalone version-control module (blobs + append-only events + rollback)
├── src/audit.py            # Post-hoc audit: Fixed vs Verified Fixed, no runtime edit restrictions
├── case/                   # Defects4J-free Java case: config.json + pristine/ + work/ (git-ignored)
├── tools/                  # new_run.py (archive a batch), show_setup_cmd.py (render a config's commands)
├── tools/selftests/        # Offline selftests (worklog, audit): no WSL, no Defects4J
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
python src/zerox_server.py
```

**4. Automated Benchmark Testing**
To run the batch testing script (which spawns the MCP client and interacts with the LLM automatically):
```bash
python src/zerox.py
```
To run the bundled single-file case instead of Defects4J, point the script at its config (`--config` is forwarded to the server via `ZEROX_CONFIG`, so both sides read the same file; relative paths are resolved against the project root, next to this README):
```bash
python src/zerox.py --config case/config.json
python src/audit.py --config case/config.json
```

Before each new batch, archive the previous batch instead of letting the numbers mix — `python tools/new_run.py` moves everything under `logs/` into `logs/archive/<timestamp>/` (keeping a `manifest.json`), clears `__pycache__`, and prints the bug list it is about to run. The archived batch stays auditable: `python src/audit.py --logs logs/archive/<timestamp>`.

**5. Bundled Single-File Case (`case/`)**
A self-testing Java program that exercises the same toolchain without Defects4J — one test run takes seconds and needs no checkout at all, so it is the fastest regression check for any change to the tools:

```text
case/
├── config.json               # target.workspace_root = "work" (relative, no absolute paths in the repo)
├── pristine/List_999.java    # the baseline; the agent never sees this directory
└── work/List_999/            # the workspace: recreated by setup_cmd, git-ignored
```

- `pristine/List_999.java` — a `SortedInts` container (binary-search bounds, counting, ranged copy, bulk removal, sorted insert, nearest lookup) plus a harness in `main`. It prints one `[PASS]` / `[FAIL]` line per case and ends with `All tests passed` or `FAILED (n)`.
- Baseline is `7/10`: the three duplicate-related cases (`count duplicates`, `last index of duplicates`, `remove all duplicates`) fail, everything else passes — one root cause, several delegated symptoms.
- No comments anywhere, so the only ground truth is the test output.
- Each case runs on a daemon thread with a 3s join timeout, so an exception or an infinite loop only fails that case.
- The baseline lives **outside** the workspace: the agent cannot see or edit it, so there is no `pristine.bak` to keep in sync by hand, and `patch_deny_globs` is unnecessary. To change the case on purpose, edit `case/pristine/<name>.java` yourself, then `rm -rf case/work`.
- Adding another single-file Java case: drop `case/pristine/<Name>.java`, add `<Name>` to `benchmark_bugs` in `case/config.json`. `setup_cmd` / `test_cmd` use `{bug_id}`, so nothing else changes. See `case/README.md` for the full recipe (including multi-file / Maven projects).

**6. Performance (WSL): keep `workspace_root` on ext4, not `/mnt/...`**
Same machine, same bug (`Chart_1b`), same Defects4J 3.0.1 — only the workspace location differs:

| Step | Workspace under `/mnt/d/...` (drvfs) | Workspace under `/home/...` (ext4) |
| :--- | :--- | :--- |
| `defects4j checkout` | 218s | — |
| `defects4j test` | 303s | **8s** (38×) |
| whole bug, end to end | 658s | **34.75s** |

Both runs are `Chart_1`, both ended `Fixed` + `clean`. The ext4 column has no separate checkout figure because the whole 34.75s already contains the checkout, two test executions and 11 LLM turns — the checkout itself is a few seconds of it.

The 38× was measured on **one** bug (n=1), but the mechanism is per-file filesystem latency, not anything Defects4J-specific, so it generalises to any many-small-files workload. It also means the "3-6 minutes per test" figures elsewhere in this README were measuring the Windows drive, not the repair loop — on ext4, wall time is dominated by LLM turns again, and the 12-test budget is no longer expensive.

Set `workspace_root` to something like `/home/<user>/zerox_workdir`. The workspace does **not** need to be visible from Windows: every read, patch and test goes through WSL commands.

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
| `workspace_root` | Root directory holding all bug workspaces, exposed as `{workspace_root}`. Three forms: `/mnt/d/work` (used as-is), `D:/work` (converted to `/mnt/d/work`), or `work` / `./work` (**relative to the config file's directory** — this is how `case/config.json` stays machine-independent). Under WSL, put it on ext4 (`/home/...`), never on `/mnt/<drive>` — see the performance note in Quick Start |
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

**Example — an existing project, tested with Maven, no checkout step** (put its workspace on ext4 under WSL, see the performance note in Quick Start):

```json
"target": {
  "tool_path": "/usr/bin/mvn",
  "workspace_root": "/home/user/work/myproject",
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
| `verified: true` / `false` | `python src/audit.py --verify` restores those test files (`git checkout --`, or `rm -f` for files the agent created), re-runs `test_cmd`, and checks `passed_pattern` again — passing with the suite restored means the fix does not lean on the edited tests |

Because the verdict is derived from the append-only log, it can be replayed over historical runs months later, without touching the workspace or spending a single test. Publish both numbers: **Fixed** and **Verified Fixed**.

**No call arbitration, no free-rerun accounting.** Both used to live in the server and both were removed deliberately: they policed the cheapest actions (a few seconds of reading, one test) while pushing the agent into random patches just to regain permission to read — and a random patch costs a whole 3-6 minute test. What is left is a single hard budget, **test executions per bug** (`agent.max_tests`, default 12), plus one line of `[预算]` bookkeeping appended to every `run_test` / `patch_and_log` reply. Patches are unlimited and free; reading is unlimited and free.

**`run_test` never resets the workspace on its own.** A checkout happens only when the caller explicitly passes `force_checkout: true` (the benchmark client does this on the first call of each bug) or when the bug id changes with no patches pending. Switching bugs while patches are pending is refused rather than silently wiping them — the old rule (`reset_task_state` flipping `IS_TASK_INITIALIZED` triggered a re-checkout) was what made the agent look *stuck at `run_test`*: every retry re-ran on freshly checked-out, still-buggy code.

> **Known blind spot:** the single-file case (`case/config.json`) keeps the code under test and the harness in the same file, so `audit_test_globs` cannot tell them apart — it is set to `[]` there, every patch audits as `clean`, and the prompt is the only constraint.

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
