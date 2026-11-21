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

## 样例规格（6 个）

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

### RateLimiter_302 · 滑动窗口限流

- 文件：`Window.java`（时间戳队列 + prune）、`RateLimiter.java`（allow）、`LimiterService.java`
- 调用链：`LimiterService.allow → RateLimiter.allow → Window.prune`
- 根因：`prune` 的裁剪条件 `<= now - windowMs` 改为 `<`
- 症状：特定时间序列下旧请求未被清除，第 N 次 allow 误判超限
- 根因类型：off-by-one（边界）；定位难度：跨方法

### JsonQuery_303 · 递归路径求值

- 文件：`JsonNode.java`（树）、`JsonParser.java`（解析）、`JsonQuery.java`（eval）、`QueryService.java`
- 调用链：`QueryService.get → JsonQuery.eval → JsonNode.child`
- 根因：数组索引解析 `substring(1, len-1)` 改为 `substring(1, len)`
- 症状：带数组的路径取值时索引解析出界或错位
- 根因类型：off-by-one（字符串边界）；定位难度：跨方法

### LruCache_304 · LRU 缓存

- 文件：`DList.java`（双向链表）、`LruCache.java`（get/put/evict）、`CacheService.java`
- 调用链：`CacheService.load → LruCache.get → DList.moveToHead`
- 根因：`LruCache.get` 命中后漏调 `moveToHead`
- 症状：get 返回值正确，但特定访问序列后淘汰了刚访问过的热点 key
- 根因类型：状态更新遗漏；定位难度：跨方法（症状不指向 get 本身）

### ExprEval_305 · 表达式求值

- 文件：`Tokenizer.java`、`ExprEvaluator.java`（调度场）、`CalcService.java`
- 调用链：`CalcService.eval → ExprEvaluator.run → Tokenizer.next`
- 根因：优先级比较 `prec(op) <= prec(top)` 改为 `<`
- 症状：同优先级运算符结合性错误，如 `a-b-c` 算成 `a-(b-c)`
- 根因类型：条件逻辑；定位难度：跨方法

### GraphPath_306 · 图最短路

- 文件：`Graph.java`（邻接表）、`Bfs.java`（shortest）、`PathService.java`
- 调用链：`PathService.distance → Bfs.shortest → Graph.neighbors`
- 根因：`visited` 标记从"入队时"挪到"出队时"
- 症状：特定图结构下同一节点重复入队，最短路距离错误
- 根因类型：状态更新时机；定位难度：跨方法

## 难度对标

| 维度 | 本方案 | Defects4J 中等 |
| :-- | :-- | :-- |
| 文件数 | 4–7 生产（含干扰）+ 1 测试 | 1–3 |
| 根因位置 | 底层，隔服务层，需从干扰链中分辨 | 单方法，偶跨文件 |
| 症状指向 | 不指向根因（含异常堆栈只指中层） | 常不指向 |
| 期望值可见 | 否（测试外置） | 测试可读但需推断 |
| 改动幅度 | 1–2 处 / 跨 1–2 文件 | 1–5 行 |

## 分工与验收

1. 我写每个文件的详细规格（字段、方法签名、语义），交 qwen3.5:4b 生成骨架与干扰类；
2. 我逐文件审阅、编译，修到"正确版本"全测试通过；
3. 注入根因后验证：buggy 版编译通过、`FAILED (n)` 部分失败、修复版 `All tests passed`；
   多处 bug 追加验证"只修一处仍失败"，异常类追加验证"只修表层仍失败"；
4. 已落地 3 个（`List_999`、`SessionStore_11`、`RenderService_7`）并写入 `benchmark_bugs`；
   其余按需实现后加入。
