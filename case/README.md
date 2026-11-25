# case/ —— 不依赖 Defects4J 的 Java 案例

ZeroX 不是 Defects4J 专用工具：任何「一条 bash 命令能编译并跑出通过/失败」的 Java 项目都能用。
这个目录就是最小示范，也是最快的回归检查（一次测试几秒，不是 3-6 分钟）。

## 目录布局

```text
case/
├── config.json                        # 案例专用配置（git 跟踪，workspace_root 写相对路径 "work"）
├── bugs/{bug_id}/                     # 每个 bug 一个自包含文件夹
│   ├── src/                           # 生产代码：setup_cmd 复制进工作区
│   │   └── *.java
│   └── test/                          # 测试代码：工作区外，agent 读不到改不到
│       └── {bug_id}Test.java
├── work/{bug_id}/                     # 工作区（.gitignore 忽略，每次 setup_cmd 重建）
├── MULTIFILE_DESIGN.md
└── README.md
```

二十个样例已按此结构落地：

- 基础三个：`List_999`（单文件生产类 `SortedInts`，7/10 基线）、`SessionStore_11`
  （3 生产 + 3 干扰，双根因）、`RenderService_7`（4 生产 + 3 干扰，空引用异常类，
  测试打印完整堆栈）。
- 高难度五个：`LruCache_304`（底层链表提前返回条件写反）、`ExprEval_305`
  （右结合运算符按左结合处理）、`GraphPath_306`（距离正确但前驱指针错）、
  `Reservation_31`（异常后状态污染）、`Checksum_29`（重试路径位运算错误）。
  每个都是 5 个生产文件（含 2 个共享关键词的干扰类）+ 1 个外置测试，
  根因在底层，症状隔一到两层才出现，且表层修法过不了测试。
- 契约型五个：`DedupQueue_401`（equals/hashCode 契约）、`TemplateEngine_402`
  （递归活跃集合未回退）、`Pager_403`（有序不变量被插入破坏）、
  `PriorityBatch_404`（比较器同优先级无 tie-break）、`CircuitBreaker_405`
  （半开计数跨状态残留）。同样是 5–6 个生产文件 + 1 个外置测试，
  根因是跨调用的契约，单看出错方法像是正确的。
- 第四批七个（状态与资源的生命周期）：`RateLimiter_302`（环形窗口回绕）、
  `JsonQuery_303`（复用游标的起始位置）、`CsvParser_406`（引号状态切换）、
  `OrderBook_407`（冻结额按剩余量释放）、`ShardRouter_408`（虚拟点移除的边删）、
  `TopoSort_409`（入度归零重复入队）、`VersionOrder_410`（分段字典序）。
  3–5 个生产文件 + 1 个外置测试；根因都在资源/状态的生命周期节点上，
  输出的主流路径（成交记录、路由结果、序列内容）保持正确，只在守恒或边界断言上暴露。

bug_id 一律用业务中性名，不在 id 中暴露根因类型。文件名与注释只描述契约与机制，
不描述"哪里错了"。详见 `MULTIFILE_DESIGN.md`。

**为什么源码 / 测试放在 `work/` 外面**

- agent 只能改 `{workspace}` 里的文件（`patch_must_be_under_workspace`），基线它既看不到也改不到，
  所以不需要 `patch_deny_globs` 这种「防自己」的配置；
- 测试外置后 agent 读不到期望值、改不到测试，`audit_test_globs` 可留 `[]`，测试天然受保护；
- 不再需要「手工保证两份文件一模一样」——那条约定的失败方式很隐蔽：改了 A 忘了同步 B，
  下一次 run 就会把新改的代码当成 pristine 覆盖掉，现象是「明明回滚了，测试却还是失败」。

## 怎么跑

```bash
python src/zerox.py  --config case/config.json    # 跑案例（几秒一次测试）
python src/audit.py  --config case/config.json    # 事后审计 clean / invalid
```

`--config` 会通过 `ZEROX_CONFIG` 传给 server，两边读同一份配置；相对路径按项目根目录解析。

## 怎么加新案例

`setup_cmd` / `test_cmd` 对结构统一处理，新增案例不用改命令：

1. 生产文件放 `bugs/{bug_id}/src/*.java`（单文件、多文件都适用；多文件时调用链分层、根因在底层）；
2. 测试放 `bugs/{bug_id}/test/{bug_id}Test.java`（默认包，内嵌 main，末行打印
   `All tests passed` 或 `FAILED (n)`，失败用例行以 `[FAIL]` 开头）；
3. 把 `{bug_id}` 加进 `case/config.json` 的 `benchmark_bugs`。

`setup_cmd` 先清空工作区，再把 `src/` 下全部 `.java` 复制进去；`test_cmd` 编译时把工作区生产文件与
工作区外的 `{bug_id}Test.java` 一起交给 javac，再运行 `{bug_id}Test`。测试文件始终在工作区外，
agent 无法读写。编译产物落在 `/tmp/zerox_{bug_id}`，多个案例不会互相踩。

已落地的 `SessionStore_11` 是双根因样例：`Session.isExpired` 的过期条件（`||` 写成 `&&`）
与 `SessionStore.touch` 用当前时间覆盖 `createdAt`；只修一处仍有测试失败。`src/` 另含 3 个共享
关键词但语义无关的干扰类（`CacheEntry` / `TokenBucket` / `RateWindow`），用于稀释关键词搜索。
设计与覆盖矩阵见 `MULTIFILE_DESIGN.md`。

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

测试已外置到工作区外，`patch_must_be_under_workspace` 使 agent 改不到测试，所以 `audit_test_globs` 写 `[]`
即可，所有 patch 判 `clean`。想验证审计对测试改动的识别能力，用 Defects4J 那套配置（`config.example.json`）。
