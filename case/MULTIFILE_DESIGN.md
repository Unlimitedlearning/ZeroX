# 多文件评测集设计（当前执行方案）

## 与原方案的区别

早期 14 个单文件样例是"填空式"：期望值内嵌在同文件，agent 直接读到答案。本方案改为多文件、调用链分层、测试外置，把难度拉到接近 Defects4J 中等水平。规模从 14 缩到 6。

## 结构约定

每个样例一个自包含文件夹，生产代码与测试分置 `src/` 与 `test/`：

```text
case/bugs/{bug_id}/
├── src/                        # 生产代码（setup_cmd 复制进工作区）
│   ├── Core.java               # 核心层：状态/算法，根因注入点
│   ├── Helper.java             # 辅助层：数据结构/词法
│   └── Service.java            # 服务层：对外接口，调用 Core
└── test/                       # 测试代码（工作区外，agent 读不到）
    └── {bug_id}Test.java       # 内嵌 main，打印 FAILED(n) / All tests passed
```

调用链统一为 `Test → Service → Core → Helper`。根因在 Core/Helper，症状在 Test，中间隔服务层——agent 必须跨文件追调用链才能定位。

**测试外置的效果**：`resolve_read_path` 限制 agent 只能读工作区内文件，所以期望值不可见；`patch_must_be_under_workspace` 让它改不到测试。这样 `audit_test_globs` 可以留空，测试代码天然受保护。

配置改动（仅 `target` 段，不动工具链）：

- `setup_cmd`：`rm -rf {workspace} && mkdir -p {workspace} && cp {workspace_root}/../bugs/{bug_id}/src/*.java {workspace}/`
- `test_cmd`：编译工作区生产文件 + `bugs/{bug_id}/test/{bug_id}Test.java`，跑 `{bug_id}Test`
- `passed_pattern` / `failed_pattern`：沿用现有 `All tests passed` / `FAILED (n)`

**干扰文件**：`src/` 除生产文件外可放若干"无关但同名"的类，与主逻辑共享关键词
（`lastAccess`、`maxInactiveMs`、`touch`、`isExpired`、`purge`），使关键词搜索命中多组平行调用链。
这些类不被测试引用、本身无 bug；agent 必须先判断哪条链与失败症状相关。它们随生产文件一起被复制和编译。

`SessionStore_11` 已按此结构落地并验证：正确版 10/10，双 bug `FAILED (6)`，只修一处分别
`FAILED (1)` / `FAILED (6)`。实际实现为双根因 + 3 个干扰类，比下表单点根因更难。

**异常类样例**：`RenderService_7` 考察异常处理能力。测试的 `execute()` 在 catch 块用
`printStackTrace` 把堆栈写入 `[FAIL]` 详情，agent 能看到抛出点与调用链。
bug_id 用业务中性名（如 `RenderService_7`），避免 `NullRef` 之类词直接剧透异常类型。

## 首批样例规格

### SessionStore_11 · 会话状态机（已落地）

- 生产文件：`Session.java`（lastAccess, createdAt, maxInactiveMs, maxLifetimeMs, isExpired, touch）、`SessionStore.java`（Map + touch + purge）、`StoreService.java`（create/get/touch）
- 干扰文件：`CacheEntry.java`、`TokenBucket.java`、`RateWindow.java`（共享 lastAccess / maxInactiveMs / touch / isExpired / purge 关键词，语义无关）
- 调用链：`StoreService.get → SessionStore.lookup → Session.isExpired`

双根因（需同时修复，改动跨 2 个文件）：

1. `Session.isExpired`：`idleExpired || lifetimeExpired` 改为 `&&` —— 两种超时任一即过期，写成两者同时满足才过期。症状：仅空闲超时或仅生命周期超时的 session 未被判过期。
2. `SessionStore.touch`：正确写法 `sessions.put(id, s.touch(now))` 改为 `new Session(id, now, now, ...)` —— 刷新时用当前时间覆盖了 `createdAt`，使生命周期时钟被重置。症状：频繁 touch 的 session 永不因生命周期到期而过期。

两处根因相互关联：`touch` 的错误会掩盖 `isExpired` 的生命周期分支，agent 必须先分离两条超时语义才能定位。
关键词搜索被 3 个干扰类稀释（如 `maxInactiveMs` 命中 3 个类的 8 处）。
- 根因类型：组合条件错误 + 状态更新时机；定位难度：跨文件 + 干扰

### RenderService_7 · 槽位渲染（已落地，异常类）

- 生产文件：`Slot.java`（值对象）、`SlotTable.java`（数组初始化 + get）、`Resolver.java`（labelAt / describeAt，解引用点）、`RenderService.java`（render / describe，入口）
- 干扰文件：`SlotCache.java`、`WindowTable.java`、`LabelRegistry.java`（共享 get(int) / size / label / resolve 关键词，语义无关）
- 调用链：`RenderService.render → Resolver.labelAt → SlotTable.get → Slot.label()`

单点根因：`SlotTable` 构造循环 `i < capacity` 改为 `i < capacity - 1` —— 最后一个槽位未初始化，`get(last)` 返回 null。
- 症状：渲染 / 描述最后一个槽位时抛 `NullPointerException`；堆栈指向 `Resolver.labelAt`（中层解引用点），不指向根因。
- Java 17 的 NPE 消息 `because the return value of "SlotTable.get(int)" is null` 指引 agent 追查 get 的返回值来源，即初始化循环。
- **只修表层无效**：在 `Resolver.labelAt` 加 null 检查返回占位符会消掉异常，但测试期望具体标签值（`[s3]`），仍 `FAILED (3)`；必须修深层初始化。
- 测试输出带完整堆栈；bug_id 用业务名避免剧透异常类型。
- 根因类型：初始化遗漏导致的空引用；考察能力：异常堆栈阅读 + 症状到根因的跨层追踪
- 验证：正确版 8/8，bug 版 `FAILED (3)`，只修表层 `FAILED (3)`，全修 `All tests passed`

### 草案中被替换的五个

`RateLimiter_302`、`JsonQuery_303`、`LruCache_304`、`ExprEval_305`、`GraphPath_306`
最初都按「单方法条件写错」设计（裁剪边界 `<=`、字符串下标、漏调 `moveToHead`、
优先级比较用 `<=`、`visited` 标记时机）。落地时这批被全部重做：这类根因落在模型
最熟悉的教科书分布上，修复率会虚高。最终规格见下文第二批（`LruCache_304`、
`ExprEval_305`、`GraphPath_306`）与第四批（`RateLimiter_302`、`JsonQuery_303`）。

## 难度对标

| 维度 | 本方案 | Defects4J 中等 |
| :-- | :-- | :-- |
| 文件数 | 3–7 生产（含干扰）+ 1 测试（`List_999` 为单文件基础样例，不计入） | 1–3 |
| 根因位置 | 底层，隔服务层，需从干扰链中分辨 | 单方法，偶跨文件 |
| 症状指向 | 不指向根因（含异常堆栈只指中层） | 常不指向 |
| 期望值可见 | 否（测试外置） | 测试可读但需推断 |
| 改动幅度 | 1–2 处 / 跨 1–2 文件 | 1–5 行 |

## 分工与验收

1. 我写每个文件的详细规格（字段、方法签名、语义），交 qwen3.5:4b 生成骨架与干扰类；
2. 我逐文件审阅、编译，修到"正确版本"全测试通过；
3. 注入根因后验证：buggy 版编译通过、`FAILED (n)` 部分失败、修复版 `All tests passed`；
   多处 bug 追加验证"只修一处仍失败"，异常类追加验证"只修表层仍失败"；
4. 首批已落地 3 个（`List_999`、`SessionStore_11`、`RenderService_7`）；第二、三批各
   落地 5 个高难度样例；第四批再落地 7 个资源生命周期型样例。共 20 个已写入
   `benchmark_bugs`。草案里另外 5 个没有按原规格实现——根因在落地时全部重做，
   见上一节与第二、四批。

## 第二批：契约型高难度样例（已落地 5 个）

### 设计原则

刻意避开 off-by-one、布尔取反这类教科书模式：生成者与修复者同源，这类 bug 落在模型
最熟悉的错误分布上，会让修复率虚高（分布泄漏）。改为**契约型**根因——单看出错方法
像是正确的，只有理解跨调用的不变量才能定位；且表层修法（消症状而非修机制）必然失败。

| Bug | 根因 | 症状出现的层 | 表层修法为何无效 |
| :-- | :-- | :-- | :-- |
| `LruCache_304` | `DList.moveToHead` 提前返回条件把「已在最前」写成「已在最后」 | 访问最久未用节点后，下一次插入淘汰错对象；`get` 返回值始终正确 | 在 `get` 加特判或改淘汰选择，仍会破坏 LRU 顺序 |
| `ExprEval_305` | 调度场把 `^` 当左结合 | 连续幂运算结果错误；单运算符、括号强制分组全过 | 改 `Math.pow` 或加括号解析都不影响结合性 |
| `GraphPath_306` | Dijkstra 松弛时 `prev[v] = prev[u]`（应为 `u`） | 距离全对，路径断裂 | 只检查距离的测试全过，必须断言完整路径 |
| `Reservation_31` | 先写库存再校验，异常路径状态已改 | 异常本身正确；异常后的库存与重试失败 | 只验证「抛了异常」的测试全过 |
| `Checksum_29` | `clear` 用异或，把未置位的 bit 置 1 | 失败任务在重试时被静默跳过 | 只检查本次运行返回值的测试抓不到 |

每个样例 5 个生产文件（含 2 个共享关键词的干扰类）+ 1 个外置测试，10–11 条断言。

### 验收方法

两道离线关卡，均不需要 WSL 或 API：

1. `python tools/selftests/case_verify.py`：把根因替换回正确实现，重编译同一份测试。
   要求 bug 版部分失败、修复版全过。实测 bug 版分别失败 4 / 3 / 6 / 3 / 6 条。
2. `python tools/selftests/case_discriminator.py`：注入 7 个「看似合理但语义错误」的
   替代实现（如永不 touch、所有运算符右结合、前驱指向自身、配额边界改 `>=`），
   要求测试全部拒绝。实测 7/7 被拒绝。

过程中两个候选变体被剔除：`LruCache` 的「总是 moveToHead」与 `Checksum` 的
「clear 为 no-op」经核对是**等价正确实现**（前者链表操作幂等，后者对未置位 bit
本就无副作用）。它们没被测试拒绝不构成漏检，反而说明断言没有过度约束实现细节。

## 第三批：跨调用契约型样例（已落地 5 个）

第二批的根因虽然不是教科书模式，仍局限在单个方法内部（条件、结合性、赋值对象）。
第三批把根因移到**跨调用的不变量**上：出错的局部代码在孤立视角下是正确的，
只有把调用链上的契约连起来才会发现矛盾。

| Bug | 被破坏的契约 | 症状出现的层 | 表层修法为何无效 |
| :-- | :-- | :-- | :-- |
| `DedupQueue_401` | `equals` 已重写但未重写 `hashCode` | 去重静默失效，同一消息被处理多次 | 换用 `List.contains` 会让 `seenCount()` 变成处理总数，仍失败 |
| `TemplateEngine_402` | 递归进入时加入活跃集合，返回时必须回退 | 同一子模板被两条分支引用时被误判为环 | 去掉环检测则真环栈溢出；放宽深度限制不改变误判 |
| `Pager_403` | 插入必须维持 id 有序，分页二分查找才成立 | 分页漏项、重复；游标停滞 | 把二分改成线性扫描能修分页，但 `snapshot()` 无序仍失败 |
| `PriorityBatch_404` | 同优先级须按到达顺序出队；`PriorityQueue` 不保证稳定 | 相同优先级任务乱序，看起来像随机 | 出队后整体反转会破坏不同优先级的顺序 |
| `CircuitBreaker_405` | 半开成功计数必须在下一次失败时清零 | 第二次熔断后只成功一次就恢复 | 提高成功阈值会连第一次恢复一起破坏 |

`DedupQueue_401` 的测试额外覆盖了一处隐蔽契约：`equals` 若忽略 `kind`，
在普通输入下 `HashSet` 仍因 `hashCode` 不同而表现正常；测试用 `Aa`/`BB`
这一对哈希碰撞的字符串让桶相同，才把这类变体逼出来。

### 验收修订

分辨力脚本改为在**修复版**源码上注入变体：先用 `case_verify` 的修复片段还原正确实现，
再替换一处为语义错误变体。原先在 bug 版上注入，基线本来就挂在那几条上，
变体是否生效无法区分，属于假阳性。修正后 18 个变体（两批共 10 个样例）全部被拒绝；
第四批接入后总数增至 32 个，覆盖全部 17 个高难度样例。

检查过程中补了三处：

- `DedupQueue` 的 `equals` 忽略 `payload` 最初没被抓到——两个不同 payload 的哈希不同，
  HashSet 根本不会调用 `equals`。补一条 payload 取 `Aa`/`BB` 的碰撞断言后才暴露。
- `CircuitBreaker` 的标称修复原本只在「CLOSED 累计失败」路径清零；HALF_OPEN 中失败回到
  OPEN 时不清零，残留计数让下一次恢复过快。修复点改到 `trip()`（所有进入 OPEN 的路径
  都经过），并补一条断言覆盖该路径。
- 另有三个变体经核对是**等价正确实现**，通过测试不算漏检：`hashCode` 只取 payload
  （等值时仍相等，碰撞合法）、重复 id 用 `<=` 插入（测试中 id 唯一的场景语义相同）、
  在 `trip()` 而非 `recordFailure` 里清零（第三条修复方案，与现方案等价）。

`case_verify` 的 bug 版失败数相应更新为 4/3/6/3/6/7/4/6/2/3（前 10 个样例）。

## 第四批：资源生命周期型样例（已落地 7 个）

七个新样例把根因放在**状态/资源的生命周期节点**上——回绕、重置、状态切换、
结算释放、删除遍历、入队条件、比较语义。共同特征是主流路径保持正确：
成交记录、路由目标、序列内容、排序结果在表层断言下都合理，只有守恒、
计数或跨位数比较暴露差异。

| 样例 | 生命周期节点 | 表层症状 |
|---|---|---|
| `RateLimiter_302` | 环形窗口回绕后最旧时间戳的读取位置 | 运行一段时间后才开始拒绝所有请求 |
| `JsonQuery_303` | 复用游标时位置归零 | 单次查询正确，批量/连续查询第二条起错位 |
| `CsvParser_406` | 引号状态的开与关 | 含引号字段内容正确，引号后的字段不再切分 |
| `OrderBook_407` | 结算时释放冻结额 | 成交价/量/笔数全对，账户可用余额虚高、冻结为负 |
| `ShardRouter_408` | 正向遍历中删除虚拟点 | `declaredReplicas` 已清零但环上残留，部分键仍路由到旧节点 |
| `TopoSort_409` | 入度归零的入队条件 | 顺序正确，输出长度多一个、目标节点重复 |
| `VersionOrder_410` | 分段的数值/文本比较语义 | 同位数版本正常，1.9 被判大于 1.10 |

验收同前两批：17 个高难度样例全部通过「bug 版部分失败 / 修复版全过」，
bug 版失败数为 4/3/6/3/6/7/4/6/2/3/2/6/3/2/4/3/5；
分辨力脚本在修复版上注入 32 个语义错误变体，实测 32/32 被拒绝。

分辨力检测中排除了两个**等价实现**（不是漏检）：拒绝时仍记录时间戳不改变已满窗口
的判定；通配符恢复位置少加 1 不改变下一次迭代（循环每轮重设 savedPos）。
另外补了一条超大键测试覆盖哈希环的回绕分支——原先的键范围 0–199 永远碰不到它，
导致「回绕到环尾」这个错误变体被漏过。

## 已知局限

这 20 个仍是 AI 生成的小规模合成样例（生产类 18–122 行，多数 40–80 行），尚未做人类盲评（HPR）、
Pass@1 难度校准或跨模型验证。定位是**可控回归集**：验证工具链改动与受控消融，
不足以支撑「agent 在真实 Java 项目上的修复能力」这类外部效度主张。
