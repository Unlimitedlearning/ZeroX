import java.util.List;

public class TopoSort_409Test {

    interface Case {
        String run();
    }

    static String check(String label, boolean ok, String detail) {
        return ok ? null : label + " -> " + detail;
    }

    static String execute(Case c) {
        try {
            return c.run();
        } catch (Throwable t) {
            return t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    static String join(List<Integer> xs) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < xs.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(xs.get(i));
        }
        return sb.toString();
    }

    /** 菱形：0 -> 1, 0 -> 2, 1 -> 3, 2 -> 3。节点 3 有两个前驱。 */
    static Graph diamond() {
        Graph g = new Graph(4);
        g.edge(0, 1);
        g.edge(0, 2);
        g.edge(1, 3);
        g.edge(2, 3);
        return g;
    }

    static Graph chain() {
        Graph g = new Graph(4);
        g.edge(0, 1);
        g.edge(1, 2);
        g.edge(2, 3);
        return g;
    }

    static Graph noEdges() {
        return new Graph(3);
    }

    static Graph cyclic() {
        Graph g = new Graph(3);
        g.edge(0, 1);
        g.edge(1, 2);
        g.edge(2, 0);
        return g;
    }

    public static void main(String[] args) {
        String[] names = {
            "chain order length",
            "chain order content",
            "no edges length",
            "diamond source first",
            "diamond target last",
            "diamond order length",
            "diamond no duplicates",
            "diamond distinct count",
            "diamond scheduler dedup",
            "cyclic order is partial",
            "cycle guard helper",
            "indeg table helper"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            List<Integer> o = new TopoSort().order(chain());
            return check("chain len", o.size() == 4, "size=" + o.size());
        });

        results[1] = execute(() -> {
            List<Integer> o = new TopoSort().order(chain());
            return check("chain order", "0,1,2,3".equals(join(o)), "got=" + join(o));
        });

        results[2] = execute(() -> {
            List<Integer> o = new TopoSort().order(noEdges());
            return check("free len", o.size() == 3, "size=" + o.size());
        });

        results[3] = execute(() -> {
            List<Integer> o = new TopoSort().order(diamond());
            return check("source first", o.get(0) == 0, "got=" + join(o));
        });

        results[4] = execute(() -> {
            List<Integer> o = new TopoSort().order(diamond());
            return check("target last", o.get(o.size() - 1) == 3, "got=" + join(o));
        });

        results[5] = execute(() -> {
            List<Integer> o = new TopoSort().order(diamond());
            return check("diamond len", o.size() == 4, "size=" + o.size() + " got=" + join(o));
        });

        results[6] = execute(() -> {
            List<Integer> o = new TopoSort().order(diamond());
            java.util.Set<Integer> s = new java.util.HashSet<>(o);
            return check("no dup", s.size() == o.size(), "got=" + join(o));
        });

        results[7] = execute(() -> {
            List<Integer> o = new TopoSort().order(diamond());
            return check("distinct", new TaskScheduler().distinctCount(o) == 4,
                "distinct=" + new TaskScheduler().distinctCount(o));
        });

        results[8] = execute(() -> {
            List<Integer> o = new TaskScheduler().rawOrder(diamond());
            return check("scheduler", o.size() == 4, "size=" + o.size());
        });

        results[9] = execute(() -> {
            List<Integer> o = new TopoSort().order(cyclic());
            return check("cyclic", o.isEmpty(), "got=" + join(o));
        });

        results[10] = execute(() -> {
            return check("guard", CycleGuard.wouldCycle(1, 1)
                    && CycleGuard.limit(-2) == 0, "guard off");
        });

        results[11] = execute(() -> {
            IndegTable t = new IndegTable(new int[]{1, 2, 0});
            return check("table", t.length() == 3 && t.at(1) == 2, "table off");
        });

        int failed = 0;
        for (int i = 0; i < names.length; i++) {
            if (results[i] == null) {
                System.out.println("[PASS] " + names[i]);
            } else {
                failed++;
                System.out.println("[FAIL] " + names[i] + ": " + results[i]);
            }
        }
        System.out.println();
        System.out.println((names.length - failed) + "/" + names.length + " test cases passed");
        if (failed == 0) {
            System.out.println("All tests passed");
        } else {
            System.out.println("FAILED (" + failed + ")");
        }
    }
}
