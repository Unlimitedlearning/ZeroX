"""离线验收自建样例：同目录编译 bug 版 / 修复版，比对测试结果。

无参运行全部高难度样例；也可单测一个：
    python tools/selftests/case_verify.py <bug_id> <bug_file> <bug_snippet> <fix_snippet>

不连 WSL、不发 API 请求。仅用本机 javac/java。
"""

import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.join(ROOT, "case", "bugs")

RESERVE_BUG = (
    '        int remaining = inventory.get(id) - quantity;\n'
    '        inventory.set(id, remaining);\n'
    '        QuotaRule.check(quantity, limit);\n'
    '        if (remaining < 0) {\n'
    '            throw new IllegalStateException("out of stock");\n'
    '        }\n'
    '        accepted++;'
)
RESERVE_FIX = (
    '        QuotaRule.check(quantity, limit);\n'
    '        int remaining = inventory.get(id) - quantity;\n'
    '        if (remaining < 0) {\n'
    '            throw new IllegalStateException("out of stock");\n'
    '        }\n'
    '        inventory.set(id, remaining);\n'
    '        accepted++;'
)

DEDUP_FIX = (
    '        return Objects.equals(kind, e.kind) && Objects.equals(payload, e.payload);\n'
    '    }\n\n'
    '    @Override\n'
    '    public int hashCode() {\n'
    '        return Objects.hash(kind, payload);'
)
TEMPLATE_BUG = (
    '            sb.append(render(ref, active));\n'
    '        }\n'
    '        return sb.toString();'
)
TEMPLATE_FIX = (
    '            sb.append(render(ref, active));\n'
    '        }\n'
    '        active.remove(name);\n'
    '        return sb.toString();'
)
PAGER_BUG = (
    '    public void insert(Record r) {\n'
    '        records.add(r);\n'
    '    }'
)
PAGER_FIX = (
    '    public void insert(Record r) {\n'
    '        int at = 0;\n'
    '        while (at < records.size() && records.get(at).id() < r.id()) {\n'
    '            at++;\n'
    '        }\n'
    '        records.add(at, r);\n'
    '    }'
)
PRIORITY_BUG = '        (a, b) -> Integer.compare(a.priority(), b.priority()));'
PRIORITY_FIX = (
    '        (a, b) -> {\n'
    '            int c = Integer.compare(a.priority(), b.priority());\n'
    '            return c != 0 ? c : Integer.compare(a.sequence(), b.sequence());\n'
    '        });'
)
CIRCUIT_BUG = (
    '    private void trip() {\n'
    '        state = BreakerState.OPEN;\n'
    '        failures = 0;\n'
    '    }'
)
CIRCUIT_FIX = (
    '    private void trip() {\n'
    '        state = BreakerState.OPEN;\n'
    '        failures = 0;\n'
    '        halfSuccesses = 0;\n'
    '    }'
)
RATE_FIX = '        return filled == capacity ? stamps[cursor] : stamps[0];'
JSON_BUG = '        cursor.reset(root, path);'
JSON_FIX = '        cursor.reset(root, path);\n        cursor.start();'
CSV_BUG = (
    '                } else {\n'
    '                    if (!inQuotes) {\n'
    '                        inQuotes = true;\n'
    '                    }\n'
    '                }'
)
CSV_FIX = '                } else {\n                    inQuotes = !inQuotes;\n                }'
ORDER_BUG = '                acct.release(b.price() * b.originalQty());'
ORDER_FIX = '                acct.release(b.price() * b.remaining());'
SHARD_BUG = (
    '        for (int i = 0; i < ring.size(); i++) {\n'
    '            if (ring.get(i).node.equals(node)) {\n'
    '                ring.remove(i);\n'
    '            }\n'
    '        }'
)
SHARD_FIX = (
    '        for (int i = ring.size() - 1; i >= 0; i--) {\n'
    '            if (ring.get(i).node.equals(node)) {\n'
    '                ring.remove(i);\n'
    '            }\n'
    '        }'
)
TOPO_BUG = '                if (indeg[to] <= 1) {'
TOPO_FIX = '                if (indeg[to] == 0) {'
VERSION_BUG = '                return Integer.toString(a).compareTo(Integer.toString(b));'
VERSION_FIX = '                return Integer.compare(a, b);'

DEFAULT_CASES = [
    ("LruCache_304", "DList.java", "if (node.next == tail) {", "if (node.prev == head) {"),
    ("ExprEval_305", "ExprEvaluator.java",
     "|| prec(ops.peek()) == prec(token))) {",
     '|| (prec(ops.peek()) == prec(token) && !"^".equals(token)))) {'),
    ("GraphPath_306", "PathFinder.java", "prev[v] = prev[u];", "prev[v] = u;"),
    ("Reservation_31", "ReservationService.java", RESERVE_BUG, RESERVE_FIX),
    ("Checksum_29", "TaskMask.java", "mask ^= bit;", "mask &= ~bit;"),
    ("DedupQueue_401", "Envelope.java",
     '        return Objects.equals(kind, e.kind) && Objects.equals(payload, e.payload);',
     DEDUP_FIX),
    ("TemplateEngine_402", "Resolver.java", TEMPLATE_BUG, TEMPLATE_FIX),
    ("Pager_403", "RecordStore.java", PAGER_BUG, PAGER_FIX),
    ("PriorityBatch_404", "JobQueue.java", PRIORITY_BUG, PRIORITY_FIX),
    ("CircuitBreaker_405", "Breaker.java", CIRCUIT_BUG, CIRCUIT_FIX),
    ("RateLimiter_302", "RingWindow.java", "        return stamps[0];", RATE_FIX),
    ("JsonQuery_303", "PathResolver.java", JSON_BUG, JSON_FIX),
    ("CsvParser_406", "CsvParser.java", CSV_BUG, CSV_FIX),
    ("OrderBook_407", "MatchingEngine.java", ORDER_BUG, ORDER_FIX),
    ("ShardRouter_408", "HashRing.java", SHARD_BUG, SHARD_FIX),
    ("TopoSort_409", "TopoSort.java", TOPO_BUG, TOPO_FIX),
    ("VersionOrder_410", "Version.java", VERSION_BUG, VERSION_FIX),
]


def run_case(bug_id, bug_file, bug_snippet, fix_snippet, verbose=True):
    base = os.path.join(BASE, bug_id)
    tmp = tempfile.mkdtemp(prefix="zxv_" + bug_id + "_")
    summary = {}
    try:
        for variant in ("buggy", "fixed"):
            dst = os.path.join(tmp, variant)
            shutil.copytree(os.path.join(base, "src"), dst)
            if variant == "fixed":
                path = os.path.join(dst, bug_file)
                src = open(path, encoding="utf-8").read()
                if bug_snippet not in src:
                    raise SystemExit(f"修复目标不存在: {bug_snippet!r}")
                open(path, "w", encoding="utf-8").write(src.replace(bug_snippet, fix_snippet, 1))

            out = os.path.join(dst, "out")
            srcs = [os.path.join(dst, f) for f in os.listdir(dst) if f.endswith(".java")]
            test_dir = os.path.join(base, "test")
            srcs += [os.path.join(test_dir, f) for f in os.listdir(test_dir) if f.endswith(".java")]
            compiled = subprocess.run(["javac", "-d", out] + srcs, capture_output=True, text=True)
            if compiled.returncode != 0:
                raise SystemExit(f"[{variant}] 编译失败:\n{compiled.stderr}")

            try:
                result = subprocess.run(
                    ["java", "-cp", out, bug_id + "Test"],
                    capture_output=True, text=True, timeout=25,
                )
            except subprocess.TimeoutExpired:
                raise SystemExit(f"[{variant}] 测试超时（可能死循环）")
            lines = result.stdout.splitlines()
            failures = [ln for ln in lines if ln.startswith("[FAIL]")]
            summary[variant] = (lines[-1] if lines else "<无输出>", failures)
            if verbose:
                print(f"=== {variant}: {summary[variant][0]}")
                for ln in failures:
                    print("   " + ln[:150])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return summary


if __name__ == "__main__":
    if len(sys.argv) == 1:
        bad = []
        for case_args in DEFAULT_CASES:
            s = run_case(*case_args, verbose=False)
            ok = s["buggy"][0] != "All tests passed" and s["fixed"][0] == "All tests passed"
            flag = "OK " if ok else "BAD"
            print(f"[{flag}] {case_args[0]:16} bug={s['buggy'][0]:14} fix={s['fixed'][0]}")
            if not ok:
                bad.append(case_args[0])
        if bad:
            raise SystemExit("失败样例: " + ",".join(bad))
    elif len(sys.argv) == 5:
        s = run_case(*sys.argv[1:])
        if s["buggy"][0] == "All tests passed":
            raise SystemExit("BUG 版全过，样例无效")
        if s["fixed"][0] != "All tests passed":
            raise SystemExit("修复版未全过，样例无效")
        print("OK: bug 版失败、修复版全过")
    else:
        raise SystemExit(__doc__)
