# case/ —— 不依赖 Defects4J 的 Java 案例

ZeroX 不是 Defects4J 专用工具：任何「一条 bash 命令能编译并跑出通过/失败」的 Java 项目都能用。
这个目录就是最小示范，也是最快的回归检查（一次测试几秒，不是 3-6 分钟）。

## 目录布局

```text
case/
├── config.json               # 案例专用配置（git 跟踪，workspace_root 写相对路径 "work"）
├── pristine/List_999.java    # 基线：没被改坏的原始版本
└── work/List_999/            # 工作区（.gitignore 忽略，每次 setup_cmd 重建）
```

**为什么基线放在 `work/` 外面**

- agent 只能改 `{workspace}` 里的文件（`patch_must_be_under_workspace`），基线它既看不到也改不到，
  所以不需要 `patch_deny_globs` 这种「防自己」的配置；
- 不再需要「手工保证两份文件一模一样」——那条约定的失败方式很隐蔽：改了 A 忘了同步 B，
  下一次 run 就会把新改的代码当成 pristine 覆盖掉，现象是「明明回滚了，测试却还是失败」。

## 怎么跑

```bash
python src/zerox.py  --config case/config.json    # 跑案例（几秒一次测试）
python src/audit.py  --config case/config.json    # 事后审计 clean / invalid
```

`--config` 会通过 `ZEROX_CONFIG` 传给 server，两边读同一份配置；相对路径按项目根目录解析。

## 怎么加第二个单文件案例

1. 放一个自包含的 Java 文件：`case/pristine/QuixBugs_Foo.java`（同一个文件里有 main 和测试）；
2. 保证它「坏」的时候会打印 `FAILED (n)`，修好后打印 `All tests passed`；
3. 把名字加进 `case/config.json` 的 `benchmark_bugs`：`["List_999", "QuixBugs_Foo"]`。

`setup_cmd` / `test_cmd` 里的 `{bug_id}` 会自动展开，命令一个字都不用改。
（编译产物落在 `/tmp/zerox_{bug_id}`，多个案例不会互相踩。）

## 换成别的项目（多文件、Maven / Gradle、原生 javac）

只改 `case/config.json` 的 `target` 段落，四件事：

| 字段 | 要写成什么 |
| :--- | :--- |
| `workspace_root` / `workspace_template` | 指到项目目录。相对路径以 `case/` 为基准，绝对路径（`/mnt/d/...` 或 `D:/...`）也支持 |
| `setup_cmd` | 怎么把项目恢复干净：`git checkout .` / 解压 fixture / 留空（工作区已就绪） |
| `passed_pattern` / `failed_pattern` | 怎么从输出里看出「全过」和「失败数」。这是唯一的裁判，写错了所有结果都会是 Failed |
| `audit_test_globs` | 哪些路径算测试代码。分不出来就写 `[]`，审计会对所有 patch 报 clean |

参考 `config.example.json`（Defects4J 那套）和根目录 `README.md` 的配置表。

## 已知盲区

单文件案例里「被测代码」和「测试代码」在同一个文件，审计无法区分，所以 `audit_test_globs` 是 `[]`：
所有 patch 都判 `clean`，唯一的约束是 `agent.user_prompt_template` 里那句「不要改 main / 测试期望值」。
想验证审计功能本身，用 Defects4J 那套配置（`config.example.json`）。
