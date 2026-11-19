# ZeroX：基于 MCP 的轻量级程序修复agent

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

## 项目概述

ZeroX 是一个基于模型上下文协议（MCP）的轻量级自动程序修复（APR）实验框架。项目将工具链缩减至 7 个，要求agent将推理过程写入外部工作日志 `work_log.jsonl`。该日志在每轮被重建为一份常驻的「活状态卡」，使上下文压缩后的恢复不依赖agent主动读取历史。

有必要首先说明本项目的定位：

- **本项目未提出新的修复算法。** 原子化修改、回滚与事后审计等机制在既有agent和版本控制工具中已广泛存在，不构成创新点。
- **本文不声称达到当前最优水平。** 下文报告的 10/10 结果基于 10 个样本、三个难度层级、单一模型及 `temperature=0` 的配置，属于初步观察，不足以支撑对修复率的一般化结论。
- **本项目的定位是一个可配置、可开关功能的对照实验平台。** 通过修改配置、运行批量Bug并比较指标，观察各组件的独立作用。

项目目标是保持实现精简、状态外置、边界清晰，以便持续改善可靠性。

## 设计取舍

以下各条为工程实现层面的取舍，并非提出新的方法范式。

**1. 工具精简与状态外置。** 仅保留 7 个 MCP 工具，强制agent将推理过程写入 `work_log.jsonl`。该日志为 `patch` / `test` / `rollback` 三类事件的 append-only 流，由独立的 `worklog.py` 模块维护；唯一的重写操作是 `rollback()`，且其在重写前先将所丢弃的事件归档至 `human_audit.jsonl`。测试结果从不被覆盖，因此日志能够给出紧凑摘要：每一轮修改了什么、**修改理由**（由agent写入）、失败数量是否呈收敛趋势（例如 `上轮 5 个 → 2 个`），而无需回放完整的 `old/new` 差异。上下文恢复的具体机制见下一节；需要强调，该恢复仅发生在同一进程内，并非跨进程检查点，进程重启的行为见「局限性」一节。

**2. 带事务的补丁写入。** `patch_and_log` 要求大型语言模型输出 `old/new` 差异，而非完整代码。每次写入均经由 `worklog.py` 执行：修改前后的内容以内容寻址方式存为 blob，一轮中的所有文件共同提交，任一文件写入失败时全部还原。`run_test` 为阻塞调用，待测试执行完毕后返回解析结果，从而避免无效的轮询。

**3. 回滚：清空上下文并保留历史。** 回滚对模型而言意味着清空上下文，以避免受既往失败轨迹干扰；被丢弃的补丁与失败轨迹则保存于 `human_audit.jsonl`，供人工分析。该机制属于上下文管理策略，不涉及新的方法设计。

**4. 观察到的自主调试现象。** 在缺少静态元数据的条件下，观察到z会向测试代码或生产代码中插入诊断探针、将运行时变量写入临时文件并在随后清理。该现象仅在个别执行轨迹中出现，不足以据此推断轻量级模型具备更强的涌现能力。我们将其整理为 `AGENTS.md` 中的操作建议，以便复用。

**5. 上下文策略：常驻活状态卡 + 价值裁剪。** 上下文管理分三层，目标是让恢复不依赖「agent记得去读日志」这一软约束：

- **活状态卡（L1）**：一条 `system` 消息，每轮请求前都从 `work_log` 重建（内容为当前推断、最近一次测试及失败数趋势、尚未测试的补丁、已排除方向），长度有上限（代码常量 `COMPACT_BRIEF_CHARS`）。它被临时拼接到请求消息的末尾，而非写入历史正文：前面的 system 提示与累积历史构成稳定前缀，可命中服务端缓存，每轮的变化只发生在末尾这一条。因此压缩裁剪不会碰到它，上下文计数也不含它；agent每一轮都带着最新状态，无需主动调用 `get_work_log` 来回忆。
- **价值裁剪（L2）**：上下文超过 `compact_context_chars` 或连续只读达到 `compact_idle_reads` 时触发压缩。压缩不再按「最近 N 轮」一刀切，而是保留最近若干轮与最近若干个**决策轮**（`patch_and_log` / `run_test` / `rollback`），优先丢弃纯搜索与读代码的闲聊轮——早期写在 `reasoning` 里的关键假设因此得以保留。裁剪始终在 `assistant` 消息边界切分，避免产生孤立的 `tool` 消息导致接口报错。超长的工具输出与补丁参数会被对称截断（补丁参数以合法 JSON 占位，防止半截 JSON 被接口拒绝）。
- **完整日志（L3）**：`work_log.jsonl` 保持不变；`get_work_log` 由必读降级为可选，仅在活状态卡不足以覆盖所需细节时调用。

上述状态来自工具调用的参数与结果，不产生额外的大模型开销。所有恢复均发生在同一进程内，详见「局限性」一节。

## 初步实验（Defects4J）

实验环境：Defects4J 3.0.1（`v3.0.1-7-g8c16da82`）、Java 11、工作区位于 ext4、模型为 DeepSeek `deepseek-flash`、`temperature=0`。下表数据来自当前批次的 `benchmark_metrics.jsonl`；`审计` 列为 `python src/audit.py` 的输出，`验证` 列为 `python src/audit.py --verify` 的输出。历史批次已归档于 `logs/archive/`，不计入本表。

| Bug编号         | 层 | 状态      | 耗时（秒） | Tokens | 入 / 出            | 轮数 | 补丁调用 | 测试次数 | 审计      | 验证 |
| :----------- | :- | :------ | :---- | :----- | :--------------- | :- | :--- | :--- | :------ | :--- |
| **Lang_10b** | A | ✅ Fixed | 106.77 | 140.4K | 132,532 / 7,890  | 1  | 1    | 2    | `clean` | ✅ `Verified Fixed` |
| **Codec_11b** | A | ✅ Fixed | 261.46 | 608.5K | 563,804 / 44,738 | 2  | 3    | 4    | `clean` | ✅ `Verified Fixed` |
| **Cli_13b** | A | ✅ Fixed | 55.16 | 289.7K | 283,548 / 6,183  | 1  | 1    | 2    | `clean` | ✅ `Verified Fixed` |
| **Csv_9b** | B | ✅ Fixed | 21.23 | 60.6K  | 58,803 / 1,812   | 1  | 1    | 2    | `clean` | ✅ `Verified Fixed` |
| **Compress_25b** | B | ✅ Fixed | 89.41 | 398.9K | 387,055 / 11,883 | 1  | 3    | 4    | `clean` | ✅ `Verified Fixed` |
| **Time_19b** | B | ✅ Fixed | 85.84 | 177.2K | 166,958 / 10,266 | 1  | 1    | 2    | `clean` | ✅ `Verified Fixed` |
| **Chart_13b** | B | ✅ Fixed | 142.41 | 387.4K | 368,194 / 19,169 | 1  | 1    | 2    | `clean` | ✅ `Verified Fixed` |
| **Time_14b** | C | ✅ Fixed | 210.18 | 890.6K | 866,166 / 24,480 | 1  | 1    | 2    | `clean` | ✅ `Verified Fixed` |
| **Compress_45b** | C | ✅ Fixed | 31.44 | 40.4K  | 39,503 / 946     | 1  | 1    | 2    | `clean` | ✅ `Verified Fixed` |
| **Math_30b** | C | ✅ Fixed | 57.86 | 27.6K  | 26,485 / 1,107   | 1  | 1    | 2    | `clean` | ✅ `Verified Fixed` |

> **10/10 Fixed · 10/10 `clean` · 10/10 `Verified Fixed`。** 十个Bug中九个在一轮内收敛，`Codec_11b` 用了两轮；测试次数为 2 次，`Codec_11b` 与 `Compress_25b` 因插入并回滚探针各为 4 次，均未触及 12 次上限；耗时 21–261 秒，合计 3.02M tokens。十个Bug覆盖 Lang、Codec、Cli、Csv、Compress、Time、Chart、Math 八个项目，tier-A 三个、tier-B 四个、tier-C 三个；构建方式同时包含 ant 与 maven。`Verified Fixed` 表示 `audit.py --verify` 复跑 `test_cmd` 后仍命中 `passed_pattern`；本批次审计均为 `clean`（未改动测试文件），故复跑为原样重放，用于排除 flaky pass。
>
> 限于样本规模与难度分布，上述结果不应作为修复率的依据。三个难度层级之间也不存在单调的代价关系：tier-C 的 `Compress_45b` 与 `Math_30b` 分别只花 40.4K 与 27.6K，属于「一眼可辨的缺陷形态」（缺 `return`、`int` 溢出）；真正拉高总量的是 tier-C 的 `Time_14b`（890.6K，占总量的 29%）与 tier-A 的 `Codec_11b`（608.5K）——两者的共同点是拿不到断言原文，只能靠通读代码或反向推导不变量收敛，代价体现为读代码的轮数而非试错的轮数。
>
> `补丁调用` 统计的是客户端观察到的 `patch_and_log` **请求次数**。本批次八个Bug为 1 次；`Codec_11b` 与 `Compress_25b` 为 3 次，其中各含 1 次与 2 次诊断探针（均已回滚，归档于 `human_audit.jsonl`）。审计输出中的 `补丁 N` 为回滚后实际生效次数，两者因此不同。
>
> 工作区所在文件系统对墙钟时间影响显著（ext4 与 Windows 盘相差可达一个数量级），详见「性能说明」一节；token 用量与轮数则取决于执行轨迹。跨环境保持稳定的只有 `Status` 与 `Audit` 两项。

### 指标文件

`logs/benchmark_metrics.jsonl` —— 每个已结束的Bug对应一行 JSON 记录，由 `src/zerox.py`（客户端）追加写入，包括崩溃和超时的运行。该记录无法由服务端生成，因为 token 用量仅对调用大型语言模型的一方可见。

| 字段                                                     | 含义                                                                                                                              |
| :----------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------ |
| `bug_id` / `bug_id_requested`                          | 均为 `benchmark_bugs` 中配置的标识符（如 `Lang_1`）。磁盘日志目录使用规范名称（`Lang_1b`），`audit.py` 会自行完成 `Lang_1` → `Lang_1b` 的映射                       |
| `status`                                               | 取值为 `Fixed` / `Failed` / `Error`。`Fixed` 表示 `run_test` 报告全部测试通过且agent停止。此前的 `MaxTurns` 状态已随 `agent.max_turns` 一同移除：收敛条件为测试上限，而非轮数 |
| `total_time_seconds`                                   | 该Bug的端到端墙钟时间                                                                                                                     |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | 该Bug所有大模型调用的 token 用量之和                                                                                                          |
| `api_calls` / `turns`                                  | 大模型请求次数 / 对话轮数                                                                                                                  |
| `tests` / `total_attempts` / `current_round`           | 测试执行次数（上限单位）、`patch_and_log` **调用次数**（被拒绝的调用亦计数，参见上文 `Time_4`）、达到的最高轮次                                                          |
| `audit` / `audit_violations`                           | `audit.py` 的事后判定：`clean` 或 `invalid`，以及导致 `invalid` 的路径                                                                         |
| `audit_version`                                        | 生成该判定的审计规则版本（`audit.AUDIT_VERSION`）。规则变更时应递增版本号，以保证同一文件中旧记录的可识别性                                                                |

### 执行记录文件

`logs/<bug>/transcript.md` —— 以纯文本记录agent的执行过程（`<bug>` 为 `bug_id_template` 生成的规范名称，因此 `Lang_1` 对应 `logs/Lang_1b/`，与同一Bug的 `work_log.jsonl` 并列存放）。内容包括：agent自身的消息（`AI`）、模型思考过程（`think`，当服务提供方返回 `reasoning_content` / `reasoning` 时）、每次工具调用及**实际发送的参数**（`call:run_test {…}`）、每次工具响应（`result:run_test`，截断至 2000 字符并注明原始长度），以及记录压缩所丢弃字符数的 `compact` 标记。每个Bug开始时该文件会被重写。

该文件存在的意义在于：上下文压缩会从对话历史中删除信息，但不会从记录中删除。运行结束后，可在不重放大模型上下文窗口的情况下查阅agent当时的推理过程。该文件**不作为任何决策的输入**：上下文压缩由 `agent.compact_context_chars`（`messages` 的字符数）触发，与此文件的大小无关，后者只单调增长。

## 基准测试集（BENCH30）

`config.example.json` 附带一份公开的 Defects4J 子集，包含 30 个Bug、8 个项目、三个难度层级，用于以较低成本开展可重复的冒烟测试。该文件中的 `benchmark_bugs` 数组为权威列表，复现实验仅需此一份配置。

### 复现步骤

```bash
cp config.example.json config.json   # 然后设置 target.tool_path + target.workspace_root
export DEEPSEEK_API_KEY=...
python src/zerox.py
```

所有与机器相关的配置（包括 `tool_path`、`workspace_root` 和凭证）均存放于 `config.json`，且已被 git 忽略，无需修改其他文件。`src/zerox.py` 以 `temperature=0` 调用模型，运行结果接近确定性；残余波动来源于服务提供方的批处理机制，因此下文仍建议开展重复实验。

项目基于 **Defects4J 3.0.1**（`v3.0.1-7-g8c16da82`，需要 Java 11）构建和验证，Bug范围为 Lang 1–65、Math 1–106、Chart 1–26、Time 1–27、Compress 1–47、Codec 1–18、Csv 1–16、Cli 1–40。Lang 18/25/48、Time 21 与 Cli 6 在上游不存在，不会被引用。

环境搭建约需 10 分钟（`init.sh` 会下载 600 MB 的项目包以及 Major / EvoSuite / Randoop 的 jar 文件）。在任何环境中均需手动执行以下步骤：官方包中的 `commons-csv` 仓库包含 CRLF blob，除非关闭空白检查，否则 Defects4J 自身的 `git apply` 无法完成 `Csv_*` 的检出——

```bash
git config --global apply.ignoreWhitespace change
```

ZeroX 本身不受该设置影响：服务端在执行差异比较和匹配 `old_str` 之前会归一化 `\r\n`。但不设置该项，Defects4J 无法检出 `Csv_*`。

### 30 个Bug

| 层级                 | 设计意图                                          | Bug                                                                                                                                                   |
| :----------------- | :-------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A — 底线探测**（8）    | 单类、1–5 行修复。若该层级低于 ≥6/8，应首先排查工具链或提示词，而非归因于Bug本身 | `Lang_1` `Lang_4` `Lang_10` `Csv_1` `Codec_5` `Codec_11` `Cli_5` `Cli_13`                                                                            |
| **B — 主要评估区间**（14） | 需要跨方法追踪或特定不变量；真实修复率主要体现在该层级（30–70% 属正常区间）     | `Lang_43` `Lang_51` `Lang_60` `Chart_1` `Chart_13` `Chart_26` `Time_4` `Time_11` `Time_19` `Csv_9` `Compress_7` `Compress_25` `Compress_38` `Cli_28` |
| **C — 压力测试**（8）    | 数值语义与二进制格式；0–2/8 属正常结果                        | `Math_2` `Math_30` `Math_63` `Math_70` `Math_73` `Math_95` `Time_14` `Compress_45`                                                                   |

分层是基于项目规模和补丁大小的**先验判断**，并非事实标签。本批次 tier-C 的 3/3 即为例证：`Compress_45` 缺一个 `return`、`Math_30` 是一次 `int` 溢出，两者的实际修复量都只有 1 行，难度更接近 tier-A；同一层级里真正吃时间的是 `Time_14`（读代码 63 轮）。因此 tier-C 的高通过率不应读作「攻克了难题」的证据。采信该分层前，应先依据元数据校准（`defects4j` 需位于 `PATH` 中）：

```bash
defects4j info -p Lang -b 1     # 查看修改的类与方法
defects4j bids -p Time         # 列出实际存在的Bug编号
```

### 有意排除的项目

Closure、JacksonDatabind、Mockito 和 Gson。单Bug测试耗时（Closure 即使在 ext4 上每次测试也需数分钟）、JDK 敏感性（Mockito）以及过大的测试集会显著拖慢 30 个Bug的扫描，并向样本引入环境噪声。这些项目留待第二阶段处理。

### 建议报告的内容

1. `logs/benchmark_metrics.jsonl` —— 每个已结束Bug一行，包含 `status`、token 用量、轮数和墙钟时间。
2. **plausible 与 correct 的区分** —— 全部测试通过仅表示补丁为 plausible，不等同于 correct。需与开发者补丁对照（`defects4j/framework/projects/<Project>/active-bugs.csv` 中的 `revision.id.fixed`），并运行 `python src/audit.py --verify`，对每个已结束Bug在恢复测试文件后重放测试：重放通过方可称为 `Verified Fixed`。
3. **方差** —— 大型语言模型具有不确定性，每个Bug应至少重复 3 次，报告均值 ± 标准差。
4. **分层与分项目比率** —— 单一汇总数字会掩盖关键信息。

`logs/` 目录下 git 仅跟踪 `logs/benchmark_metrics.jsonl`、`logs/audit.jsonl` 和 `logs/<bug>/human_audit.jsonl`，从而保证已发布数字可审计，同时避免提交全部临时日志。若 tier C 的结果显著高于文献值，应先运行 `python src/audit.py` 并检查 `invalid` 记录，再作进一步解读。

## 目录结构

```text
ZeroX/
├── .gitignore              # 忽略 venv、__pycache__、logs、真实配置和本地草稿区
├── local/                  # 本地草稿区：调试 / 实验 / 临时产物，整目录被 git 忽略
├── README.md               # 本文档
├── AGENTS.md               # 系统提示词：工具、上限、工作流（核心资产）
├── config.example.json     # 配置模板
├── requirements.txt        # 固定版本的运行依赖
├── src/                    # 全部代码
├── src/zerox.py            # agent入口：大模型循环、上限记账、指标（唯一写入方）
├── src/zerox_server.py     # MCP 服务端：访问工作区的 7 个工具
├── src/zerox_core/         # 包：配置默认值与校验、工具 schema、命令执行
├── src/worklog.py          # 独立版本控制模块（blob + append-only 事件 + 回滚）
├── src/audit.py            # 事后审计：Fixed 与 Verified Fixed，运行时不修改文件
├── case/                   # 不依赖 Defects4J 的 Java 单文件样例：config.json + pristine/ + work/
├── tools/                  # new_run.py（归档批次）、show_setup_cmd.py（渲染配置中的命令）
├── tools/selftests/        # 离线自测（路径、worklog、审计），无需 WSL 和 Defects4J
├── logs/                   # 运行日志、工作日志、blob 存储及基准指标
```

## 快速开始

**1. 依赖与安装**

需要 Python 3.10+ 以及兼容 MCP 的客户端（如 Claude Desktop、Cursor 或 Nanobot）。建议使用项目本地虚拟环境，使 ZeroX 的依赖与其他工具隔离：

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

依赖版本固定于 `requirements.txt`（已在 Python 3.11.9 上验证）。下述所有 `python ...` 命令均在激活后的环境中执行。

**2. 配置**

复制模板，填入实际绝对路径和大型语言模型凭证：

```bash
cp config.example.json config.json
```

（同时支持 Windows WSL 和原生 Linux 环境。）

`config.json` 已在 `.gitignore` 中忽略，含本机绝对路径或 API 凭证的配置文件不应提交。若需为单台机器保留本地设置（例如本地模型端点），可另存为 `config.local.json` 并使用 `--config` 指定，该类文件同样被忽略：

```bash
python src/zerox.py --config config.local.json
```

配置中的 `llm_config.base_url` 支持 OpenAI 兼容端点。将 `base_url` 指向本地推理服务（如 Ollama 的 `http://localhost:11434/v1`）即可接入本地模型；由于代码会直接读取 `api_key` 字段，本地服务无需真实密钥时填任意占位值（如 `"ollama"`）即可。需要注意，工具调用能力取决于模型本身：实测本地 4B 级模型能稳定调用工具，但未能修复本项目的真实Bug，详见「局限性」一节。

调试脚本、实验数据等本机产物统一放入 `local/`（例如一次复测的源码与日志放 `local/<实验名>/`）。该目录已被 `.gitignore` 整体忽略，提交时无需逐一检查。

**3. 启动 MCP 服务端**

```bash
python src/zerox_server.py
```

**4. 批量测试**

批量脚本会启动 MCP 客户端并自动与大型语言模型交互：

```bash
python src/zerox.py
```

若要运行自带的单文件样例而非 Defects4J，可将脚本指向对应配置（`--config` 会通过 `ZEROX_CONFIG` 转发给服务端，确保两侧读取同一份配置；相对路径按项目根目录解析，即本 README 所在目录）：

```bash
python src/zerox.py --config case/config.json
python src/audit.py --config case/config.json
```

每批运行前应归档上一批，避免数据混淆：`python tools/new_run.py` 会将 `logs/` 下全部内容移至 `logs/archive/<timestamp>/`（并生成 `manifest.json`），清除 `__pycache__`，并打印即将运行的Bug列表。归档批次仍可审计：`python src/audit.py --logs logs/archive/<timestamp>`。

**事后审计与验证。** 批量运行结束后，分两步判定结果。第一步只读 `work_log.jsonl`，不运行任何测试，给出 `clean` / `invalid`（是否改过测试文件）；第二步才复跑测试，确认修复不是 flaky pass：

```bash
python src/audit.py               # 第一步：审计，秒级
python src/audit.py --verify      # 第二步：复跑 test_cmd，每个Bug 2-20 秒（冷工作区需完整编译，3-6 分钟）
python src/audit.py --verify --bug Lang_1   # 只验证单个Bug（前缀匹配）
```

`--verify` 的行为取决于审计结论：若为 `invalid`（补丁动过测试文件），先用 `git checkout --` / `rm -f` 还原这些文件再复跑；若为 `clean`，则原样复跑。两种情况下都要求复跑命中 `passed_pattern` 才记为 `Verified Fixed`。复跑通过 WSL 执行 `defects4j test`，耗时主要受工作区文件系统影响（ext4 上远快于 `/mnt/...`，见性能说明）；实时输出写入 `logs/<bug>/verify.log`，可用 `tail -f` 查看进度。结果以追加方式写入 `logs/audit.jsonl`，每行一个Bug，含 `verified` 与 `verified_reason`；加 `--no-write` 可只看不写。

复跑是在**当前工作区**上重放 `test_cmd`，不重新 apply 补丁，也不校验工作区是否只剩日志记录过的改动：它回答的是「这份工作区现在还通过吗」，而非「从干净代码出发能否复现这次修复」。多轮迭代留下的残留改动（例如未回滚的探针）会被一并放过，因此探针必须走 `rollback` 而不是手工删除。

> Windows 下必须能在 PATH 中找到 `wsl` 且 WSL 内已装好 Defects4J，否则复跑会报 `WinError 5 拒绝访问` 并记为未复现 —— 这不代表修复失败，只是验证环境不可用。

**5. 单文件样例（`case/`）**

该样例为一个自带测试的 Java 程序，使用同一套工具链但不依赖 Defects4J —— 单次测试仅需数秒且无需检出，是修改工具后最快捷的回归检查：

```text
case/
├── config.json               # target.workspace_root = "work"（相对路径，仓库中不含绝对路径）
├── pristine/List_999.java    # 基线文件；agent不可见
└── work/List_999/            # 工作区：由 setup_cmd 重建，git 忽略
```

- `pristine/List_999.java` —— 一个 `SortedInts` 容器（二分边界、计数、区间复制、批量删除、有序插入、最近查找）及 `main` 中的测试驱动程序。每条用例打印一行 `[PASS]` / `[FAIL]`，最后输出 `All tests passed` 或 `FAILED (n)`。
- 基线结果为 `7/10`：三个与重复元素相关的用例（`count duplicates`、`last index of duplicates`、`remove all duplicates`）失败，其余通过 —— 单一根因对应多个表象。
- 代码中不含任何注释，测试输出是唯一的判定依据。
- 每条用例运行于守护线程，3 秒 join 超时，因此异常或死循环仅影响该条用例。
- 基线文件位于工作区**外部**：agent无法读取或修改，因而无需维护 `pristine.bak` 的同步，也无需配置 `patch_deny_globs`。如需有意修改该样例，请直接编辑 `case/pristine/<name>.java`，随后执行 `rm -rf case/work`。
- 新增单文件 Java 样例：将 `case/pristine/<Name>.java` 放入目录，并把 `<Name>` 加入 `case/config.json` 的 `benchmark_bugs`。`setup_cmd` / `test_cmd` 使用 `{bug_id}`，其余配置无需变更。完整说明（含多文件 / Maven 项目）见 `case/README.md`。

**6. 性能说明（WSL）：将 `workspace_root` 置于 ext4 而非 `/mnt/...`**

在同一机器、同一Bug（`Chart_1b`）、同一 Defects4J 3.0.1 环境下，仅工作区位置不同：

| 步骤                   | 工作区位于 `/mnt/d/...`（drvfs） | 工作区位于 `/home/...`（ext4） |
| :------------------- | :------------------------ | :---------------------- |
| `defects4j checkout` | 218s                      | —                       |
| `defects4j test`     | 303s                      | **8s**（38×）             |
| 单个Bug端到端              | 658s                      | **34.75s**              |


两次运行均为 `Chart_1`，均以 `Fixed` + `clean` 结束。ext4 一列未列出单独的检出耗时，因为 34.75 秒已包含检出、两次测试执行和 11 轮大模型调用 —— 检出本身仅占其中数秒。

上述 38 倍差异基于单个Bug测得（n=1），但其机制为逐文件系统的访问延迟，并非 Defects4J 特有，因而适用于一般性的大量小文件负载。这也说明，本文档其他章节提到的「单次测试 3–6 分钟」测量的是 Windows 盘环境，而非修复循环本身：在 ext4 上，墙钟时间重新由大模型轮数主导，12 次测试的上限不再昂贵。

应将 `workspace_root` 设置为类似 `/home/<user>/zerox_workdir` 的路径。工作区**无需**在 Windows 中可见：所有读取、修改和测试均通过 WSL 命令完成。

## 配置参考（更换测试套件无需修改代码）

所有环境相关配置集中于 `config.json`，代码仅读取该文件。配置分为四部分：

| 部分 | 用途 |
| :--- | :--- |
| `target` | 代码位置、准备方式、测试方式及判定规则 |
| `agent` | 行为参数与提示词 —— 需要频繁调整的部分 |
| `timeouts` | `test_execution`（服务端）与 `llm_client`（客户端） |
| `llm_config` / `benchmark_bugs` | 模型凭证与Bug列表 |

### `target` —— 工作区、命令与判定

| 字段 | 含义 |
| :--- | :--- |
| `tool_path` | 测试框架 / 构建工具入口，在命令中表示为 `{tool}` |
| `workspace_root` | 所有Bug工作区的根目录，在命令中表示为 `{workspace_root}`。支持三种形式：`/mnt/d/work`（原样使用）、`D:/work`（转换为 `/mnt/d/work`）、`work` / `./work`（**相对于配置文件所在目录** —— `case/config.json` 借此保持机器无关性）。在 WSL 下应置于 ext4（`/home/...`），而非 `/mnt/<drive>`，参见快速开始中的性能说明 |
| `workspace_template` | 单个Bug的目录模板，在命令中表示为 `{workspace}` |
| `setup_cmd` | 每个新Bug执行一次（检出 / 解压 / 切换分支）。留空表示无需准备。重新检出会同时清除该Bug的 `work_log.jsonl`，因为工作区已回到初始状态，既有轮次不再适用 |
| `test_cmd` | 每次验证时执行 |
| `require_workspace` | 增量运行前是否检查 `{workspace}` 是否存在 |
| `source_glob` | `search_code` 使用的默认文件通配符 |
| `bug_id_pattern` | 解析 `bug_id` 的正则表达式；**每个命名分组均成为可用占位符** |
| `bug_id_template` | 规范Bug名称（用于日志及 `work_log.jsonl`） |
| `default_version_suffix` | 当模式未提供 `suffix` 分组时，追加至 `{number}` 的后缀 |
| `passed_pattern` | 命中即表示全部测试通过、Bug已修复 |
| `failed_pattern` | 命中即表示测试失败；可选命名分组 `count`（整数）与 `detail`（堆栈信息） |
| `patch_allow_globs` | 可选白名单（`fnmatch` 语法）。**默认为空**，表示工作区内任意路径均可修改 |
| `patch_deny_globs` | 可选黑名单。命中即拒绝，优先级高于白名单。默认为空 |
| `patch_must_be_under_workspace` | 拒绝路径位于 `{workspace}` 之外的补丁（默认 `true`）。相对路径先按 `{workspace}` 解析再校验；未逃出工作区的 `..` 允许使用，归一化后逃逸的路径被拒绝（读取与修改共用 `zerox_core.paths`） |
| `audit_test_globs` | 界定哪些路径算作测试代码 —— **仅 `audit.py` 在运行结束后读取** |

可用占位符包括：`{tool}`、`{workspace_root}`、`{workspace}`、`{bug_id}`，以及 `bug_id_pattern` 的全部命名分组（如 `{project}`、`{number}`、`{version}`、`{suffix}`）。`setup_cmd` 与 `test_cmd` 中的工作区路径会自动添加 shell 引号。

**示例 —— 已有项目，使用 Maven 测试，无需检出步骤**（按性能说明，工作区应置于 WSL 的 ext4 下）：

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

**示例 —— 每个Bug一个目录，标识符直接作为目录名（`quixbugs-quicksort` → `{workspace_root}/quixbugs-quicksort`）：**

```json
"workspace_template": "{workspace_root}/{bug_id}",
"bug_id_pattern": "^(?P<bug_id>[A-Za-z0-9_\\-]+)$",
"bug_id_template": "{bug_id}",
"setup_cmd": "",
"test_cmd": "cd {workspace} && python run_tests.py 2>&1",
"passed_pattern": "All tests passed",
"failed_pattern": "FAILED \\((?P<count>\\d+)"
```

### 运行时不限制文件修改，改由事后审计

ZeroX 有意不在agent运行期间限制其可修改的文件范围。唯一的运行时约束是「位于 `{workspace}` 内」：`patch_and_log` 先将相对路径按该目录解析（`src/main/java/...` 形式可用），归一化 `..`，仅拒绝归一化后逃出工作区的路径。读取工具使用 `zerox_core.paths` 中的同一解析器，因此 `src/main/../main/Foo.java` 在读取和修改时均合法。

早期版本曾设置两道约束 —— `src/main/java` 白名单，以及每次测试前重新校验的测试树 `md5` 基线 —— 现均已移除，原因如下：

- 白名单会拒绝合法补丁。模型通常以 `src/main/java/...` 而非绝对路径给出目标，每次拒绝都会导致agent为重新获得权限而随机提交补丁；而单次测试耗时 3–6 分钟。
- 基线方案需要额外四个配置字段（`test_guard_suffixes`、`test_guard_ignore_dirs` 等）才能避免将 Defects4J 构建产物误判为测试文件改动。

判定逻辑已迁移至 `audit.py`：它读取运行结束后的 `work_log.jsonl`，给出两个相互独立的标签：

| 标签 | 含义 |
| :--- | :--- |
| `audit: clean` / `invalid` | `invalid` 表示至少有一轮修改了匹配 `target.audit_test_globs` 的文件 |
| `verified: true` / `false` | `python src/audit.py --verify` 重跑 `test_cmd` 并再次检查 `passed_pattern`。若审计为 `invalid`，先还原相关测试文件（对已有文件执行 `git checkout --`，对agent新建的文件执行 `rm -f`）再复跑；若审计为 `clean`，则原样复跑。复跑通过即 `verified: true`（同时记录 `verified_reason` 与 `reverted` 列表），用于排除 flaky pass 与对测试的依赖 |

由于判定由 append-only 日志推导得出，数月后仍可在历史运行上重放，无需触碰工作区，也无需消耗测试次数。两项结果均应报告：**Fixed** 与 **Verified Fixed**。

**无调用仲裁，无免费重跑计数。** 二者此前均位于服务端，现均已移除：它们约束的是代价最低的操作（数秒的读取、一次测试），却导致agent为重新获得读取权限而随机提交补丁 —— 而一次随机补丁需要消耗完整的 3–6 分钟测试。目前仅保留一项硬性上限：每个Bug的**测试执行次数**（`agent.max_tests`，默认 12），达到上限时服务端返回【任务失败】并终止任务。该计数只在服务端生效，不向 agent 报告剩余次数 —— 将测试描述为需要节省的资源，会使 agent 回避验证，与「补丁免费、测试是唯一可靠信号」的提示词相矛盾。补丁数量与读取次数均不受限制。

**`run_test` 不会自行重置工作区。** 仅当调用方显式传入 `force_checkout: true`（基准客户端在每个Bug的首次调用时如此设置），或Bug标识符发生变化且无待处理补丁时，才会执行检出。存在待处理补丁时切换Bug会被拒绝，而非静默清除 —— 旧规则（`reset_task_state` 翻转 `IS_TASK_INITIALIZED` 触发重新检出）正是agent表现为「停滞在 `run_test`」的原因：每次重试都在刚刚检出、仍含Bug的代码上重新执行。

> **已知盲点：** 单文件样例（`case/config.json`）将被测代码与测试驱动程序置于同一文件，`audit_test_globs` 无法区分二者，因此该配置中将其设为 `[]`，所有补丁均审计为 `clean`，仅依靠提示词约束。

### `agent` —— 行为参数与提示词

| 配置项 | 含义 |
| :--- | :--- |
| `max_tests` | 每个Bug最多执行 `run_test` 的次数（服务端），唯一的硬性上限 |
| `compact_context_chars` | 触发一次历史压缩的上下文大小（字符数，非 token）—— 取代旧的「每 20 轮」规则；后者在遇到庞大测试日志时压缩过晚，在廉价的只读轮次上压缩过早 |
| `system_prompt_file` | 提示词文件，相对于项目根目录（绝对路径亦可用） |
| `user_prompt_template` | 第一条用户消息，其中 `{bug_id}` 会被替换 |
| `fixed_marker` / `failed_marker` | agent结束任务必须输出的标记；须与 `AGENTS.md` 保持一致 |

## 局限性

**不支持跨进程恢复。** 活状态卡与压缩机制恢复的是丢失的**上下文** —— 压缩发生后，agent依靠每轮重建的状态卡与保留的决策轮在同一进程内继续执行。它并非检查点：大模型对话、token 计数和轮次状态仅存于 `src/zerox.py` 进程内。重启脚本将从 `benchmark_bugs` 的第一项开始新一轮批次，不提供 `--resume` 选项。具体而言，每个Bug开始时客户端在首次 `run_test` 设置 `force_checkout: true` 并重写 `transcript.md`，服务端在重新检出工作区时清除该Bug的 `work_log.jsonl` —— 因此重启一个Bug会丢失进行中的补丁历史，从干净的含Bug代码重新开始。运行中途崩溃仅留下 `logs/<bug>/crash.txt`；未达到退出状态的Bug不会向 `benchmark_metrics.jsonl` 写入记录。因此，批量运行应在一个会话中完成，或从头重新执行。

**小模型能够调用工具，并不意味着能够修复Bug。** 本地 4B 级模型（如 qwen3.5:4b）在工具调用方面表现稳定，但在本项目的实验中未能定位并修复 Lang_1 —— 其在搜索步骤上反复循环。该现象表明，稳定的工具调用能力与修复真实Bug的能力属于两个不同层面，不应以前者推断后者。

**覆盖范围有限。** 目前仅在 10 个Bug上完成验证，涉及八个项目、两种构建方式（ant 与 maven）和 tier-A / tier-B / tier-C 三个难度层级。更大样本、跨模型配置以及未公开Bug尚未验证。

---

*ZeroX 由广东工业大学大一新生独立开发。*
