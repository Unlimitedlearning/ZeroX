"""假 bug 检测：用「看似合理但语义错误」的替代实现打测试，检验测试分辨力。

替代实现注入在**修复版**源码上：先按 case_verify 的修复片段还原正确实现，
再替换一处为语义错误变体。若测试全过，说明断言只认症状、认不出语义错误。
在 bug 版上注入会得到假阳性——基线本来就挂在那几条上，变体是否生效无法区分。

    python tools/selftests/case_discriminator.py

不发请求、不连 WSL。
"""

import os
import shutil
import subprocess
import sys
import tempfile

from case_verify import DEFAULT_CASES

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.join(ROOT, "case", "bugs")
FIXES = {c[0]: (c[1], c[2], c[3]) for c in DEFAULT_CASES}

REVERSE_OLD = (
    '            int c = Integer.compare(a.priority(), b.priority());\n'
    '            return c != 0 ? c : Integer.compare(a.sequence(), b.sequence());'
)
REVERSE_NEW = (
    '            int c = Integer.compare(b.priority(), a.priority());\n'
    '            return c != 0 ? c : Integer.compare(a.sequence(), b.sequence());'
)

# (bug_id, 替代实现名, 文件, 原片段, 替换片段)
VARIANTS = [
    ("LruCache_304", "never touch on get", "LruCache.java",
     "list.moveToHead(node);", "// access does not reorder"),
    ("LruCache_304", "existing key keeps stale value", "LruCache.java",
     "            existing.value = value;", "            // stale value kept"),
    ("ExprEval_305", "every operator right-associative", "ExprEvaluator.java",
     '|| (prec(ops.peek()) == prec(token) && !"^".equals(token)))) {',
     '|| (prec(ops.peek()) == prec(token)))) {'),
    ("GraphPath_306", "predecessor points to self", "PathFinder.java",
     "prev[v] = u;", "prev[v] = v;"),
    ("Reservation_31", "quota boundary excludes equality", "QuotaRule.java",
     "if (quantity > limit) {", "if (quantity >= limit) {"),
    ("Checksum_29", "clear behaves like mark", "TaskMask.java",
     "mask &= ~bit;", "mask |= bit;"),
    ("Checksum_29", "successful item is not marked", "BatchRunner.java",
     "                mask.mark(item.bit());", "                // not marked"),
    ("DedupQueue_401", "equals ignores kind", "Envelope.java",
     "        return Objects.equals(kind, e.kind) && Objects.equals(payload, e.payload);",
     "        return Objects.equals(payload, e.payload);"),
    ("DedupQueue_401", "accepted order reversed", "DedupQueue.java",
     "        accepted.add(e);", "        accepted.add(0, e);"),
    ("DedupQueue_401", "equals ignores payload", "Envelope.java",
     "        return Objects.equals(kind, e.kind) && Objects.equals(payload, e.payload);",
     "        return Objects.equals(kind, e.kind);"),
    ("TemplateEngine_402", "no cycle detection", "Resolver.java",
     "        if (!active.add(name)) {", "        if (false) {"),
    ("TemplateEngine_402", "missing template silently empty", "Resolver.java",
     '            throw new IllegalArgumentException("missing: " + name);',
     '            return "";'),
    ("Pager_403", "after ignores cursor", "RecordPager.java",
     "            if (all.get(mid).id() <= lastId) {", "            if (false) {"),
    ("Pager_403", "page size off by one", "RecordPager.java",
     "out.size() < size", "out.size() < size + 1"),
    ("PriorityBatch_404", "reverse priority order", "JobQueue.java",
     REVERSE_OLD, REVERSE_NEW),
    ("PriorityBatch_404", "drain reverses all jobs", "BatchRunner.java",
     "            out.add(queue.poll().id());", "            out.add(0, queue.poll().id());"),
    ("CircuitBreaker_405", "open circuit allows calls", "Breaker.java",
     "        return state != BreakerState.OPEN;", "        return true;"),
    ("CircuitBreaker_405", "cooldown is a no-op", "Breaker.java",
     "        if (state == BreakerState.OPEN) {", "        if (false) {"),
    ("RateLimiter_302", "oldest always first slot", "RingWindow.java",
     "        return filled == capacity ? stamps[cursor] : stamps[0];", "        return stamps[0];"),
    ("RateLimiter_302", "window never slides", "SlidingLimiter.java",
     "        if (window.filled() == capacity && window.oldest() > now - windowMs) {",
     "        if (window.filled() == capacity) {"),
    ("JsonQuery_303", "cursor start missing on batch", "PathResolver.java",
     "        cursor.reset(root, path);\n        cursor.start();", "        cursor.reset(root, path);"),
    ("JsonQuery_303", "wildcard visits first element only", "PathResolver.java",
     "            for (int k = 0; k < n; k++) {", "            for (int k = 0; k < 1; k++) {"),
    ("CsvParser_406", "quote never closes", "CsvParser.java",
     "                    inQuotes = !inQuotes;", "                    inQuotes = true;"),
    ("CsvParser_406", "comma splits inside quotes", "CsvParser.java",
     "            } else if (c == ',' && !inQuotes) {", "            } else if (c == ',') {"),
    ("OrderBook_407", "release uses original quantity", "MatchingEngine.java",
     "                acct.release(b.price() * b.remaining());",
     "                acct.release(b.price() * b.originalQty());"),
    ("OrderBook_407", "trade uses buy price", "MatchingEngine.java",
     "new Trade(b.id(), s.id(), s.price(), qty)", "new Trade(b.id(), s.id(), b.price(), qty)"),
    ("ShardRouter_408", "remove scans forward", "HashRing.java",
     "        for (int i = ring.size() - 1; i >= 0; i--) {", "        for (int i = 0; i < ring.size(); i++) {"),
    ("ShardRouter_408", "route wraps to last point", "HashRing.java",
     "        return ring.get(0).node;", "        return ring.get(ring.size() - 1).node;"),
    ("TopoSort_409", "enqueue at one remaining", "TopoSort.java",
     "                if (indeg[to] == 0) {", "                if (indeg[to] <= 1) {"),
    ("TopoSort_409", "skip final predecessors", "TopoSort.java",
     "            for (int to : g.successors(v)) {", "            for (int to : new java.util.ArrayList<Integer>()) {"),
    ("VersionOrder_410", "segment compared as text", "Version.java",
     "                return Integer.compare(a, b);",
     "                return Integer.toString(a).compareTo(Integer.toString(b));"),
    ("VersionOrder_410", "ignore segments after first", "Version.java",
     "        int n = Math.max(parts.length, other.parts.length);",
     "        int n = 1;"),
]


def try_variant(bug_id, label, filename, old, new):
    base = os.path.join(BASE, bug_id)
    fix_file, bug_snippet, fix_snippet = FIXES[bug_id]
    tmp = tempfile.mkdtemp(prefix="zxd_" + bug_id + "_")
    try:
        dst = os.path.join(tmp, "src")
        shutil.copytree(os.path.join(base, "src"), dst)
        fix_path = os.path.join(dst, fix_file)
        fixed = open(fix_path, encoding="utf-8").read()
        if bug_snippet not in fixed:
            return None, f"修复目标不存在: {bug_snippet!r}"
        open(fix_path, "w", encoding="utf-8").write(
            fixed.replace(bug_snippet, fix_snippet, 1))

        path = os.path.join(dst, filename)
        src = open(path, encoding="utf-8").read()
        if old not in src:
            return None, f"替换目标不存在（修复版）: {old!r}"
        open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))

        out = os.path.join(tmp, "out")
        srcs = [os.path.join(dst, f) for f in os.listdir(dst) if f.endswith(".java")]
        test_dir = os.path.join(base, "test")
        srcs += [os.path.join(test_dir, f) for f in os.listdir(test_dir) if f.endswith(".java")]
        compiled = subprocess.run(["javac", "-d", out] + srcs, capture_output=True, text=True)
        if compiled.returncode != 0:
            return None, "编译失败: " + compiled.stderr.strip().splitlines()[0]
        try:
            result = subprocess.run(
                ["java", "-cp", out, bug_id + "Test"],
                capture_output=True, text=True, timeout=25,
            )
        except subprocess.TimeoutExpired:
            return "[FAIL] test hung (killed after 25s)", None
        lines = [ln for ln in result.stdout.splitlines() if ln.startswith("[FAIL]")]
        return lines[-1] if lines else None, None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    selected = VARIANTS
    if len(sys.argv) > 1:
        indices = [int(x) for x in sys.argv[1:]]
        selected = [VARIANTS[i] for i in indices]
    caught = 0
    for bug_id, label, filename, old, new in selected:
        failure, error = try_variant(bug_id, label, filename, old, new)
        if error:
            print(f"[?] {bug_id:16} {label:38} {error}")
        elif failure:
            caught += 1
            print(f"[✓] {bug_id:16} {label:38} 抓住: {failure[:80]}")
        else:
            print(f"[!] {bug_id:16} {label:38} 漏掉：测试全过")
    print(f"\n{caught}/{len(selected)} 个替代实现被测试拒绝")
    sys.exit(0 if caught == len(selected) else 1)
