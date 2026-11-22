# 配置参考

全部环境配置集中于 `config.json`，代码仅读取该文件，更换测试套件无需修改代码。

| 部分 | 用途 |
| :--- | :--- |
| `target` | 代码位置、准备方式、测试方式及判定规则 |
| `agent` | 行为参数与提示词 —— 需频繁调整的部分 |
| `timeouts` | `test_execution`（服务端）与 `llm_client`（客户端） |
| `llm_config` / `benchmark_bugs` | 模型凭证与Bug列表 |

## `target` —— 工作区、命令与判定

| 字段 | 含义 |
| :--- | :--- |
| `tool_path` | 构建 / 测试工具入口，在命令中为 `{tool}` |
| `workspace_root` | 工作区根目录，在命令中为 `{workspace_root}`。三种形式：`/mnt/d/work`（原样）、`D:/work`（转为 `/mnt/d/work`）、`work` / `./work`（**相对配置文件所在目录**，`case/config.json` 借此保持机器无关）。WSL 下应置于 ext4 |
| `workspace_template` | 单个Bug的目录模板，在命令中为 `{workspace}` |
| `setup_cmd` | 每个新Bug执行一次（检出 / 解压 / 切换分支），留空表示无需准备。重新检出会清除该Bug的 `work_log.jsonl` |
| `test_cmd` | 每次验证时执行 |
| `require_workspace` | 增量运行前是否检查 `{workspace}` 是否存在 |
| `source_glob` | `search_code` 的默认文件通配符 |
| `bug_id_pattern` | 解析 `bug_id` 的正则；**每个命名分组均成为占位符** |
| `bug_id_template` | 规范Bug名称（用于日志与 `work_log.jsonl`） |
| `default_version_suffix` | 模式未提供 `suffix` 分组时，追加至 `{number}` 的后缀 |
| `passed_pattern` | 命中即表示全部通过、Bug已修复 |
| `failed_pattern` | 命中即表示失败；可选命名分组 `count`（整数）与 `detail`（堆栈） |
| `patch_allow_globs` | 可选白名单（`fnmatch`）。默认空，表示工作区内任意路径可改 |
| `patch_deny_globs` | 可选黑名单，命中即拒绝，优先级高于白名单 |
| `patch_must_be_under_workspace` | 拒绝位于 `{workspace}` 之外的补丁（默认 `true`）。相对路径先按 `{workspace}` 解析；未逃出工作区的 `..` 允许，归一化后逃逸则拒绝（读写共用 `zerox_core.paths`） |
| `audit_test_globs` | 界定测试代码路径 —— **仅 `audit.py` 在运行结束后读取** |

可用占位符：`{tool}`、`{workspace_root}`、`{workspace}`、`{bug_id}` 及 `bug_id_pattern` 的全部命名分组（如 `{project}`、`{number}`、`{version}`、`{suffix}`）。`setup_cmd` 与 `test_cmd` 中的工作区路径自动加 shell 引号。

**示例 —— 已有项目，Maven 测试，无需检出**（工作区应置于 WSL 的 ext4）：

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

**示例 —— 每个Bug一个目录**（`quixbugs-quicksort` → `{workspace_root}/quixbugs-quicksort`）：

```json
"workspace_template": "{workspace_root}/{bug_id}",
"bug_id_pattern": "^(?P<bug_id>[A-Za-z0-9_\\-]+)$",
"bug_id_template": "{bug_id}",
"setup_cmd": "",
"test_cmd": "cd {workspace} && python run_tests.py 2>&1",
"passed_pattern": "All tests passed",
"failed_pattern": "FAILED \\((?P<count>\\d+)"
```

## 运行时不限制文件修改，改由事后审计

运行期唯一的路径约束是「位于 `{workspace}` 内」：相对路径按该目录解析（`src/main/java/...` 可用），归一化 `..`，仅拒绝归一化后逃逸的路径；读写共用 `zerox_core.paths` 的解析器。早期版本的两道约束已移除 —— `src/main/java` 白名单会拒绝合法补丁（模型通常以相对路径给出目标，每次拒绝都会导致agent为恢复权限而随机提交补丁，而单次测试在 Windows 盘上耗时 3–6 分钟）；测试树 `md5` 基线则需额外四个配置字段（如 `test_guard_suffixes`、`test_guard_ignore_dirs`）才能避免把构建产物误判为测试改动。

判定移至 `audit.py`：读取运行结束后的 `work_log.jsonl`，给出 `clean` / `invalid`（后者表示至少有一轮修改了匹配 `audit_test_globs` 的文件）与 `verified: true` / `false`（`--verify` 重跑 `test_cmd`，`invalid` 时先还原测试文件，通过则记录 `verified_reason` 与 `reverted`）。判定由 append-only 日志推导，数月后仍可在历史运行上重放，无需触碰工作区或消耗测试次数。两项结果均应报告：**Fixed** 与 **Verified Fixed**。

服务端不设调用仲裁与免费重跑计数：二者约束的是代价最低的操作（数秒的读取、一次测试），却使agent为恢复权限而随机提交补丁。仅保留一项硬性上限 —— 每个Bug的**测试执行次数**（`agent.max_tests`，默认 12），达到上限时返回【任务失败】并终止。该计数不向agent报告剩余次数：把测试描述为需要节省的资源会使agent回避验证，与「补丁免费、测试是唯一可靠信号」的提示词相矛盾。

**`run_test` 不会自行重置工作区。** 仅当调用方注入 `force_checkout: true`（基准客户端在任务首跑注入一次；该参数不在工具 schema 里，模型无法主动要求重置），或Bug标识符变化且无待处理补丁时才执行检出；存在待处理补丁时切换Bug会被拒绝而非静默清除 —— 旧规则（`reset_task_state` 翻转 `IS_TASK_INITIALIZED` 触发重新检出）正是agent「停滞在 `run_test`」的原因：每次重试都运行在刚检出、仍含Bug的代码上。

> **已知盲点：** 单文件样例（`case/config.json`）将被测代码与测试驱动程序置于同一文件，`audit_test_globs` 无法区分二者，故该配置中设为 `[]`，所有补丁均审计为 `clean`，仅依靠提示词约束。

## `agent` —— 行为参数与提示词

| 配置项 | 含义 |
| :--- | :--- |
| `max_tests` | 每个Bug最多执行 `run_test` 的次数（服务端），唯一的硬性上限 |
| `compact_context_chars` | 触发压缩的上下文大小（字符数，非 token）；取代旧的「每 20 轮」规则，后者在庞大测试日志下压缩过晚、在廉价的只读轮次上压缩过早 |
| `system_prompt_file` | 提示词文件，相对于项目根目录 |
| `user_prompt_template` | 第一条用户消息，`{bug_id}` 会被替换 |
| `fixed_marker` / `failed_marker` | 结束任务必须输出的标记，须与 `AGENTS.md` 一致 |
