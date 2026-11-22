# ZeroX：基于 MCP 的轻量级程序修复agent

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

## 项目概述

ZeroX 是一个基于模型上下文协议（MCP）的自动程序修复（APR）实验框架。工具链限定为 7 个，要求agent将推理写入外部工作日志 `work_log.jsonl`；该日志每轮被重建为常驻的「活状态卡」，使上下文压缩后的恢复不依赖agent主动读取历史。

- **未提出新的修复算法。** 原子化修改、回滚与事后审计在既有agent与版本控制工具中已广泛存在，不构成创新点。
- **不声称达到当前最优水平。** 下文 26/26 的结果受训练数据泄漏影响（见「初步实验」），不能作为修复率或成本的依据。
- **定位是可配置、可开关功能的对照实验平台。** 通过修改配置、批量运行Bug并比较指标，观察各组件的独立作用。

## 设计取舍

以下是工程实现层面的取舍，并非新的方法范式。

**1. 工具精简与状态外置。** 工作日志是 `patch` / `test` / `rollback` 三类事件的 append-only 流，由 `worklog.py` 维护；唯一的重写操作 `rollback()` 在重写前将丢弃的事件归档至 `human_audit.jsonl`。测试结果从不被覆盖，摘要因而能给出每轮改动、修改理由与失败数趋势（如 `测试趋势：5 → 2 个失败`），无需回放 `old/new` 差异。

**2. 带事务的补丁写入。** `patch_and_log` 用行号锚点定位修改：模型给出起始行、该行一段不小于 8 字符的原文片段与新内容，服务端校验锚点后才替换，无需抄写含缩进的原文；若原文括号配平而补丁后失衡则整轮拒绝，把语法错误挡在落盘前（不自动补齐，由模型据提示自行修正）。前后内容以内容寻址存为 blob，一轮内所有文件共同提交，任一失败则全部还原。`run_test` 为阻塞调用，结束后返回解析结果；失败时服务端本地解析失败用例的源文件位置、方法体与 stderr 尾部（探针输出不再需要写文件绕路）。

**3. 回滚：清空上下文并保留历史。** 对模型而言回滚即清空上下文，避免受既往失败轨迹干扰；被丢弃的补丁与轨迹存于 `human_audit.jsonl`。

**4. 观察到的自主调试现象。** 缺少静态元数据时，agent会插入诊断探针、将运行时变量写入临时文件并在随后清理。该现象仅见于个别轨迹，不足以推断涌现能力；相关建议整理在 `AGENTS.md`。

**5. 上下文策略：常驻活状态卡 + 价值裁剪。** 分三层：

- **活状态卡（L1）**：一条 `system` 消息，每轮从 `work_log` 重建（当前推断、最近测试与失败数趋势、未测试的补丁、已排除方向），长度受 `COMPACT_BRIEF_CHARS` 限制。它拼接在请求末尾而非写入历史正文：前面的提示与累积历史构成稳定前缀，可命中服务端缓存；压缩不裁剪它，上下文计数也不含它。
- **价值裁剪（L2）**：超过 `compact_context_chars` 或连续只读达到 `compact_idle_reads` 时触发，保留最近若干轮与最近若干决策轮（`patch_and_log` / `run_test` / `rollback`），优先丢弃纯搜索与读代码的轮次，使早期 `reasoning` 中的假设得以保留。裁剪在 `assistant` 边界切分，避免孤立 `tool` 消息导致接口报错；超长输出与补丁参数对称截断（补丁参数以合法 JSON 占位）。
- **完整日志（L3）**：`work_log.jsonl` 不变；`get_work_log` 降级为可选，仅在状态卡不足时调用。

状态全部来自工具调用的参数与结果，不产生额外模型开销；恢复仅发生在同一进程内，见「局限性」。压缩时从 `thoughts.jsonl` 取最近 3 条 think 原文回传模型，每轮实际注入的状态卡写入 `l1_state.jsonl`，见 [`docs/logs.md`](docs/logs.md)。

**6. 首轮裁剪工具集（固定前缀）。** 工具的 JSON Schema 随每次请求重发，是固定前缀中占比最高的一段（7 个工具合计 3.2K 字符）。客户端仅在第一轮裁剪：`INIT` 只发送 `run_test`，取得测试结论后进入 `READY` 并固定为全量七工具，此后不再变化。之所以只分两阶段、不按动作细分，是因为 `tools` 数组一旦改变，服务端前缀缓存即自断点失效——同一段对话实测，工作集固定时第 3 次请求命中 2,432 token，按动作交替工作集时仅命中 1,536 token、切换后的首次请求仅 8%；26 题批次共 92 次切换，其重算成本高于裁剪 schema 的收益。首轮裁剪无此代价：该次请求缓存必然为空，且 `INIT_TOOLS` 是 `READY_TOOLS` 的前缀，首轮的 tools 段可在次轮继续命中。实测前缀（system + user + tools）由 2,670 token/次降至首轮 708、其后 1,691，全批 3.56M → 3.24M（−9.1%）。越阶段调用在客户端拦下并回传可用工具清单；连续两轮未调用工具则恢复全量，避免窄工具集阻塞批量运行。逻辑见 `src/zerox_core/phases.py`（纯函数、离线可测）。

**7. 搜索命中直接给出声明范围。** `search_code` 在方法/构造函数的命中行尾附上该声明的 `[起-止]` 行号，模型不必再查一次文件结构即可按范围读取。结构解析复用 `get_file_internal_structure` 同一份索引（`java_structure.py`）；源码由服务端一次批量回读（单次搜索最多 5 个文件、每个只取前 4000 行，用固定前缀分隔行拼接），回读失败、越界或文件过大时该条不标注，结果照常返回。依据是此前执行记录中读取区间中位 71 行而所在方法中位 20 行、83% 的读取越出方法边界：标注只增加十余字符和一次回读，减少的是方法外的代码。尚未做批次对照，收益幅度待测。

## 初步实验（Defects4J）

实验环境：Defects4J 3.0.1（`v3.0.1-7-g8c16da82`）、Java 11、工作区位于 ext4、模型为 DeepSeek `deepseek-flash`、`temperature=0`。数据来自当前批次的 `benchmark_metrics.jsonl`；`审计` 与 `验证` 分别为 `python src/audit.py` 与 `python src/audit.py --verify` 的输出。历史批次归档于 `logs/archive/`，不计入本表。表中 `Tokens` 由引入首轮裁剪之前的版本测得（固定前缀 2,670 token/次），同批重跑时该部分约低 9%，其余指标不受影响。

| Bug编号 | 状态 | 耗时（秒）| Tokens | 轮次 | 对话轮数 | 补丁请求 | 测试次数 | 审计 | 验证 |
| :--- | :--- | :--- | :--- | :- | :- | :- | :- | :--- | :--- |
| **Lang_1b** | ✅ Fixed | 81.8 | 66.5K | 1 | 7 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Codec_5b** | ✅ Fixed | 32.5 | 116.7K | 1 | 10 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Csv_1b** | ✅ Fixed | 18.5 | 53.2K | 1 | 9 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Cli_5b** | ✅ Fixed | 15.7 | 28.6K | 1 | 6 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Chart_1b** | ✅ Fixed | 25.8 | 31.5K | 1 | 7 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Time_4b** | ✅ Fixed | 85.8 | 171.1K | 1 | 12 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Compress_7b** | ✅ Fixed | 26.8 | 63.0K | 1 | 11 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Math_30b** | ✅ Fixed | 170.6 | 185.5K | 3 | 12 | 3 | 3 | `clean` | ✅ `Verified Fixed` |
| **Lang_4b** | ✅ Fixed | 70.8 | 39.9K | 2 | 8 | 2 | 2 | `clean` | ✅ `Verified Fixed` |
| **Cli_13b** | ✅ Fixed | 39.1 | 99.7K | 1 | 11 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Chart_13b** | ✅ Fixed | 116.5 | 386.0K | 3 | 18 | 3 | 3 | `clean` | ✅ `Verified Fixed` |
| **Csv_9b** | ✅ Fixed | 20.9 | 55.1K | 1 | 10 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Time_11b** | ✅ Fixed | 98.3 | 320.5K | 1 | 17 | 2 | 3 | `clean` | ✅ `Verified Fixed` |
| **Compress_25b** | ✅ Fixed | 68.4 | 234.9K | 1 | 16 | 2 | 3 | `clean` | ✅ `Verified Fixed` |
| **Math_63b** | ✅ Fixed | 77.9 | 51.2K | 1 | 8 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Lang_43b** | ✅ Fixed | 81.7 | 85.3K | 1 | 10 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Time_19b** | ✅ Fixed | 69.9 | 114.0K | 1 | 10 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Compress_38b** | ✅ Fixed | 963.6 | 209.5K | 3 | 15 | 3 | 4 | `clean` | ✅ `Verified Fixed` |
| **Cli_28b** | ✅ Fixed | 18.3 | 60.6K | 3 | 11 | 3 | 3 | `clean` | ✅ `Verified Fixed` |
| **Math_70b** | ✅ Fixed | 26.3 | 32.6K | 1 | 7 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Lang_51b** | ✅ Fixed | 31.5 | 29.9K | 1 | 6 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Time_14b** | ✅ Fixed | 204.5 | 885.7K | 1 | 50 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Compress_45b** | ✅ Fixed | 62.1 | 66.1K | 1 | 7 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Math_73b** | ✅ Fixed | 38.2 | 76.3K | 1 | 9 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Lang_60b** | ✅ Fixed | 28.7 | 24.9K | 1 | 6 | 1 | 2 | `clean` | ✅ `Verified Fixed` |
| **Math_95b** | ✅ Fixed | 36.4 | 73.8K | 1 | 11 | 1 | 2 | `clean` | ✅ `Verified Fixed` |

> **26/26 Fixed · 26/26 `clean` · 26/26 `Verified Fixed`。** 覆盖 BENCH30 中除上批已修的 4 个之外的全部 26 个Bug、8 个项目；合计 3.56M tokens，墙钟约 42 分钟。列的含义：`轮次` 为回滚后留存的补丁轮次（`current_round`）；`对话轮数` 为模型请求次数（`turns`）；`补丁请求` / `测试次数` 取 metrics 的 `total_attempts` / `tests`，含已回滚的诊断探针。
>
> **这批结果不能作为修复率或成本的依据——存在明显的训练数据泄漏。** 26 个Bug中 21 个的执行记录在 think 原文里直接出现 "known bug" / "I recall" / "Defects4J `<id>` fix" 等措辞：模型认出这些是公开基准中的已知Bug，并从训练记忆中回忆官方修复，而非从失败测试与源码现场推理。补丁与官方 developer fix 高度吻合（如 `Chart_1` 的 `dataset != null → == null`、`Lang_51` 补一个 `break`、`Math_70` 的 `solve(min,max) → solve(f,min,max)`）。19/26 一个补丁即命中，全批 2 元成本、6–50 轮，均显著低于真实未知Bug的预期。因此 100% 的 fix rate 与低成本是**记忆效应，不是修复能力的证据**；`Verified Fixed` 只说明补丁使测试通过且未改测试文件，这一结论仍然成立。
>
> 若要评估真实能力，需使用训练数据中未出现过的Bug（如自建 `case/` 样例或非公开项目）。工具链本身不依赖 Defects4J 特定语义，配置中的 `setup_cmd` / `test_cmd` / `passed_pattern` 可指向任意 Java 项目，适配其他Java只是替换工作区与测试脚本；但"能运行"与"能修复"是两个问题，后者仍需无泄漏的评测集验证。
>
> 单Bug耗时受文件系统影响显著（`Compress_38b` 的 963.6 秒主要为 checkout 与测试等待，token 仅 209.5K，见「性能说明」）；跨环境稳定的只有 `Status` 与 `Audit`。上批 4 个Bug的数据已归档于 `logs/archive/`，不计入本表。

指标文件 `logs/benchmark_metrics.jsonl` 的字段定义、执行记录 `logs/<bug>/transcript.md` 的格式与 `logs/` 的版本控制范围，见 [`docs/logs.md`](docs/logs.md)。

## 历史基准（Defects4J / BENCH30）

> **该测试集已弃用，不再作为推荐评测集。** 30 个 Bug 均为公开基准，模型在训练数据中已见过其修复，26/26 的结果存在明显泄漏（见「初步实验」），无法反映真实修复能力。后续评测以自建样例（`case/`）为主。本节仅保留历史背景与复现方法；`config.example.json` 中的 `benchmark_bugs` 数组是 Defects4J 的配置示例，不代表推荐测试集。

### 复现步骤

按「快速开始」安装依赖并复制配置，随后将 `target.tool_path`、`target.workspace_root` 指向本节所述环境。`temperature=0` 使结果接近确定，残余波动来自服务端批处理，仍建议重复实验。

项目基于 **Defects4J 3.0.1**（`v3.0.1-7-g8c16da82`，需 Java 11）构建与验证，Bug范围为 Lang 1–65、Math 1–106、Chart 1–26、Time 1–27、Compress 1–47、Codec 1–18、Csv 1–16、Cli 1–40；Lang 18/25/48、Time 21 与 Cli 6 在上游不存在。环境搭建约需 10 分钟（`init.sh` 下载 600 MB 项目包及 Major / EvoSuite / Randoop 的 jar）。官方 `commons-csv` 仓库含 CRLF blob，需关闭空白检查，否则 Defects4J 自身的 `git apply` 无法完成 `Csv_*` 检出：

```bash
git config --global apply.ignoreWhitespace change
```

ZeroX 不受该设置影响：服务端在应用锚点补丁前会归一化 `\r\n`；但不设置该项，Defects4J 无法检出 `Csv_*`。

### 建议报告的内容

除 `benchmark_metrics.jsonl` 外，报告应区分 **plausible 与 correct**（测试全通过仅为 plausible，需与 `active-bugs.csv` 中的 `revision.id.fixed` 对照，并以 `--verify` 重放测试，通过方可称为 `Verified Fixed`）；给出**方差**（每个Bug至少重复 3 次，报告均值 ± 标准差）；并按**项目与改动量档**分别统计，而非只给一个汇总数字。

若结果显著高于文献值，应先运行 `python src/audit.py` 并检查 `invalid` 记录，确认没有改测试文件或泄漏。

## 目录结构

```text
ZeroX/
├── .gitignore              # 忽略 venv、__pycache__、logs、真实配置和本地草稿区
├── local/                  # 本地草稿区：调试 / 实验 / 临时产物，整目录被 git 忽略
├── AGENTS.md               # 系统提示词：工具、上限、工作流（核心资产）
├── docs/                   # config.md（配置）、logs.md（记录格式）、resume.md（中断续跑）
├── config.example.json     # 配置模板
├── requirements.txt        # 固定版本的运行依赖
├── src/zerox.py            # agent入口：大模型循环、上限计数、指标（唯一写入方）
├── src/zerox_server.py     # MCP 服务端：访问工作区的 7 个工具
├── src/zerox_core/         # 无状态纯函数：配置 / schema / 阶段 / 命令执行 + 各工具底层（搜索/诊断/结构/补丁）
├── src/worklog.py          # 独立版本控制模块（blob + append-only 事件 + 回滚）
├── src/audit.py            # 事后审计：Fixed 与 Verified Fixed，运行时不修改文件
├── case/                   # 不依赖 Defects4J 的 Java 样例：config.json + bugs/{id}/{src,test}/ + work/
├── tools/                  # new_run.py（归档批次）、selftests/（离线自测，无需 WSL）
└── logs/                   # 运行日志、工作日志、blob 存储及基准指标
```

## 快速开始

**1. 依赖与安装**

需要 Python 3.10+ 以及兼容 MCP 的客户端（如 Claude Desktop、Cursor 或 Nanobot）。建议使用项目本地虚拟环境隔离依赖：

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

依赖版本固定于 `requirements.txt`（已在 Python 3.11.9 上验证）。下文的 `python ...` 命令均在激活后的环境中执行。

**2. 配置**

```bash
cp config.example.json config.json
```

`config.json` 已被忽略，含本机绝对路径或凭证的配置不应提交；需为单台机器保留设置（如本地模型端点）时，可另存为 `config.local.json` 并以 `--config` 指定，该类文件同样被忽略。调试脚本与实验数据放入 `local/`，该目录整体被忽略。

`llm_config.base_url` 支持 OpenAI 兼容端点，指向本地推理服务（如 Ollama 的 `http://localhost:11434/v1`）即可接入本地模型；`api_key` 会被直接读取，本地服务可填占位值（如 `"ollama"`）。工具调用能力取决于模型本身：实测 4B 级模型能稳定调用工具，但未能修复本项目的真实Bug，详见「局限性」。

**3. 启动 MCP 服务端**

```bash
python src/zerox_server.py
```

**4. 批量测试**

批量脚本启动 MCP 客户端并自动与模型交互：

```bash
python src/zerox.py
```

运行自带样例时指向对应配置（`--config` 会通过 `ZEROX_CONFIG` 转发给服务端，相对路径按项目根目录解析）：

```bash
python src/zerox.py --config case/config.json
python src/audit.py --config case/config.json
```

每批运行前应归档上一批：`python tools/new_run.py` 将 `logs/` 全部内容移至 `logs/archive/<timestamp>/`（含 `manifest.json`），清除 `__pycache__` 并打印待运行列表；归档批次仍可审计（`--logs logs/archive/<timestamp>`）。

中断续跑：批量中跳过已完成用 `--skip-done`；单个 bug 内续跑用 `--resume-bug <id>`（从 `work_log` 恢复进度，不重新 checkout）。两者的用法与前提见 `docs/resume.md`。

**事后审计与验证。** 第一步只读 `work_log.jsonl`，不运行测试，给出 `clean` / `invalid`；第二步复跑测试，确认修复不是 flaky pass：

```bash
python src/audit.py               # 第一步：审计，秒级
python src/audit.py --verify      # 第二步：复跑 test_cmd，每个Bug 2–22 秒（冷工作区需完整编译，3–6 分钟）
python src/audit.py --verify --bug Lang_1   # 只验证单个Bug（前缀匹配）
```

审计自建样例时须显式传 `--config case/config.json`（或设 `ZEROX_CONFIG`）：默认配置是 Defects4J，其 `bug_id_pattern` 也能匹配 `List_999` 这类名字，但工作区指向不存在的 `d4j_*` 目录。审计脚本会打印实际使用的配置路径，并在复跑前检查工作区是否存在，配置失配时直接给出原因而非误报「复跑未通过」。

`--verify` 在 `invalid` 时先还原被改动的测试文件（`git checkout --` / `rm -f`）再复跑，`clean` 时原样复跑；命中 `passed_pattern` 才记为 `Verified Fixed`。输出写入 `logs/<bug>/verify.log`（可 `tail -f`），结果追加至 `logs/audit.jsonl`，`--no-write` 只读不写。复跑是**在当前工作区**上重放 `test_cmd`，不重新 apply 补丁：它回答「这份工作区现在是否通过」，而非「从干净代码能否复现这次修复」；残留改动（如未回滚的探针）不会被识别，故探针必须走 `rollback`。

> Windows 下需保证 PATH 中存在 `wsl` 且 WSL 内已装好 Defects4J，否则复跑会报 `WinError 5 拒绝访问` 并记为未复现 —— 这不代表修复失败，只是验证环境不可用。

**5. 自建 Java 样例（`case/`）**

使用同一套工具链但不依赖 Defects4J：单次测试仅需数秒，是修改工具后最快的回归检查。每个 bug 一个自包含文件夹，源码与测试分置：

```text
case/
├── config.json                    # target.workspace_root = "work"（相对路径，不含绝对路径）
├── bugs/{bug_id}/src/*.java       # 生产代码；setup_cmd 复制进工作区，agent 改这里
├── bugs/{bug_id}/test/*Test.java  # 测试代码；工作区外，agent 读不到也改不到
└── work/{bug_id}/                 # 工作区：由 setup_cmd 重建，git 忽略
```

- 已落地三个样例：`List_999`（单文件生产类 `SortedInts`，基线 `7/10`，单一根因对应多个表象）、`SessionStore_11`（3 生产 + 3 干扰，双根因，只修一处仍失败）、`RenderService_7`（4 生产 + 3 干扰，空引用异常类，测试打印堆栈，只修表层仍失败）。
- 测试输出契约统一：每条用例打印 `[PASS]` / `[FAIL]`，末行输出 `All tests passed` 或 `FAILED (n)`；判定以输出正则为准，不依赖进程退出码。
- 源码与测试都在工作区**外部**，agent 只能改 `work/{bug_id}/` 里的副本，故无需 `patch_deny_globs`。`setup_cmd` 每次先清空工作区再复制，避免陈旧文件干扰。
- 新增样例：按 `bugs/{bug_id}/src` 与 `bugs/{bug_id}/test` 放好文件，把 `{bug_id}` 加入 `case/config.json` 的 `benchmark_bugs` 即可，`setup_cmd` / `test_cmd` 无需改动。细节见 `case/README.md` 与 `case/MULTIFILE_DESIGN.md`。

本批次结果（`deepseek-flash`，`temperature=0`，审计与复跑均 clean / Verified Fixed）：

| Bug | 状态 | 耗时（秒）| Tokens | 轮次 | 对话轮数 | 审计 | 验证 |
| :-- | :-- | --: | --: | --: | --: | :-- | :-- |
| List_999 | Fixed | 14.64 | 24,606 | 2 | 8 | clean | Verified Fixed |
| SessionStore_11 | Fixed | 14.18 | 35,665 | 2 | 11 | clean | Verified Fixed |
| RenderService_7 | Fixed | 9.24 | 23,750 | 1 | 8 | clean | Verified Fixed |

合计 3/3 修复、84,021 tokens、38.06 秒，均为引入首轮裁剪工具集之后测得（固定前缀首轮 708 token、其后 1,691）。`SessionStore_11` 的双根因（组合条件 + touch 重置生命周期）须两处同改，`RenderService_7` 的堆栈指向中层解引用点、根因在深层初始化循环——仅加 null 检查仍失败。

**6. 性能说明（WSL）：将 `workspace_root` 置于 ext4 而非 `/mnt/...`**

同一机器、同一Bug（`Chart_1b`）、同一环境下仅工作区位置不同：

| 步骤                   | `/mnt/d/...`（drvfs） | `/home/...`（ext4） |
| :------------------- | :------------------ | :---------------- |
| `defects4j checkout` | 218s                | —                 |
| `defects4j test`     | 303s                | **8s**（38×）       |
| 单个Bug端到端              | 658s                | **34.75s**        |

两次运行均为 `Chart_1`，均以 `Fixed` + `clean` 结束；ext4 列未单列检出耗时，因 34.75 秒已含检出、两次测试与 11 轮模型调用。38 倍差异基于单个Bug（n=1），机制是逐文件系统的访问延迟，适用于一般性大量小文件负载。本文其他位置的「单次测试 3–6 分钟」（`audit.py --verify` 的注释与 [`docs/config.md`](docs/config.md)）测量的是 Windows 盘环境；在 ext4 上墙钟时间由模型轮数主导，12 次测试上限不再构成瓶颈。工作区**无需**在 Windows 中可见，读写与测试均通过 WSL 完成。

## 配置参考

全部环境配置集中于 `config.json`，代码仅读取该文件，更换测试套件无需修改代码。`target` 的 16 个字段、占位符规则与两套示例（Maven 现成项目、每Bug一目录）、`agent` 的行为参数，见 [`docs/config.md`](docs/config.md)。

三项行为约束需在此说明：

1. **运行期唯一的路径约束是「位于 `{workspace}` 内」**，是否改动测试文件由 `audit.py` 事后判定；早期版本的 `src/main/java` 白名单与测试树 `md5` 基线已移除，原因见 [`docs/config.md`](docs/config.md)。
2. **唯一的硬性上限是每个Bug的测试执行次数**（`agent.max_tests`，默认 12），且不向agent报告剩余次数 —— 把测试描述为需要节省的资源会使agent回避验证，与「补丁免费、测试是唯一可靠信号」的提示词相矛盾。补丁数量与读取次数不受限制。
3. **`run_test` 不会自行重置工作区**，仅当基准客户端在任务首跑注入 `force_checkout: true`（该参数不在工具 schema 中，模型无法主动要求重置，见 [`docs/config.md`](docs/config.md)）或Bug变化且无待处理补丁时才检出；存在待处理补丁时切换Bug会被拒绝而非静默清除。

## 局限性

**不支持跨进程恢复。** 活状态卡与压缩恢复的是丢失的**上下文**，并非检查点：模型对话、token 计数与轮次状态仅存于 `src/zerox.py` 进程内。重启脚本从 `benchmark_bugs` 首项开始新批次，不提供 `--resume`；每个Bug开始时客户端在首次 `run_test` 设置 `force_checkout: true` 并重写 `transcript.md`，服务端重新检出时清除该Bug的 `work_log.jsonl`，故重启单个Bug会丢失进行中的补丁历史。运行中途崩溃仅留下 `logs/<bug>/crash.txt`，未达退出状态的Bug不写入 `benchmark_metrics.jsonl`；批量运行应在单个会话内完成。

**小模型能调用工具，并不意味着能修复Bug。** 本地 4B 级模型（如 qwen3.5:4b）在工具调用上表现稳定，但未能定位并修复 Lang_1 与 Lang_4：两题分别运行 27 与 30 轮，各只调用 1 次 `run_test`、**从未调用 `patch_and_log`**，调用集中于读代码与搜索（`read_specific_code` 占 19 与 23 次）。稳定的工具调用能力与修复真实Bug的能力属于两个层面，不应以前者推断后者。

**覆盖范围有限。** 当前批次 26 个Bug覆盖 8 个项目，但均为 ant 构建，且结果受训练数据泄漏影响（见「初步实验」）；难度分布偏向单轮收敛 —— 19/26 一个补丁即命中，其余 7 个为 2–3 轮（如 `Math_30b`、`Chart_13b`、`Cli_28b`、`Compress_38b`）。更大样本、跨模型配置、非 ant 项目与未公开Bug尚未验证。

---

*ZeroX 由广东工业大学大一新生独立开发。*
