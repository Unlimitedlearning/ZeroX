import java.util.Arrays;

public class Checksum_29Test {

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

    static WorkItem item(String name, int bit, boolean fails) {
        return new WorkItem(name, bit, fails);
    }

    public static void main(String[] args) {
        String[] names = {
            "all items succeed",
            "failed item stays pending",
            "retry processes the failed item",
            "retry success completes mask",
            "completed item is not reprocessed",
            "failure ordered before success",
            "repeated failures stay pending",
            "failure counter increments once",
            "unrelated bit is not set by failure",
            "clear leaves marked bit untouched"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            BatchRunner r = new BatchRunner();
            int n = r.run(Arrays.asList(item("a", 1, false), item("b", 2, false)));
            return check("all succeed", n == 2 && r.mask() == 3, "n=" + n + " mask=" + r.mask());
        });

        results[1] = execute(() -> {
            BatchRunner r = new BatchRunner();
            int n = r.run(Arrays.asList(item("a", 1, false), item("b", 2, true)));
            return check("pending", n == 1 && !r.done(2), "n=" + n + " done(2)=" + r.done(2));
        });

        results[2] = execute(() -> {
            BatchRunner r = new BatchRunner();
            r.run(Arrays.asList(item("a", 1, false), item("b", 2, true)));
            int n = r.run(Arrays.asList(item("b", 2, false)));
            return check("retry", n == 1, "expected 1, got " + n);
        });

        results[3] = execute(() -> {
            BatchRunner r = new BatchRunner();
            r.run(Arrays.asList(item("a", 1, false), item("b", 2, true)));
            r.run(Arrays.asList(item("b", 2, false)));
            return check("complete mask", r.mask() == 3, "expected 3, got " + r.mask());
        });

        results[4] = execute(() -> {
            BatchRunner r = new BatchRunner();
            r.run(Arrays.asList(item("a", 1, false)));
            int n = r.run(Arrays.asList(item("a", 1, false)));
            return check("no reprocess", n == 0, "expected 0, got " + n);
        });

        results[5] = execute(() -> {
            BatchRunner r = new BatchRunner();
            int n = r.run(Arrays.asList(item("b", 2, true), item("a", 1, false)));
            return check("order", n == 1 && r.mask() == 1, "n=" + n + " mask=" + r.mask());
        });

        results[6] = execute(() -> {
            BatchRunner r = new BatchRunner();
            r.run(Arrays.asList(item("b", 2, true)));
            int n = r.run(Arrays.asList(item("b", 2, true)));
            return check("repeat fail", n == 0 && !r.done(2), "n=" + n + " done(2)=" + r.done(2));
        });

        results[7] = execute(() -> {
            BatchRunner r = new BatchRunner();
            r.run(Arrays.asList(item("a", 1, true)));
            return check("counter", r.failures() == 1 && r.processed() == 0,
                "failures=" + r.failures() + " processed=" + r.processed());
        });

        results[8] = execute(() -> {
            BatchRunner r = new BatchRunner();
            r.run(Arrays.asList(item("a", 1, false)));
            r.run(Arrays.asList(item("c", 4, true)));
            return check("other bit", r.done(1) && !r.done(4), "done(1)=" + r.done(1) + " done(4)=" + r.done(4));
        });

        results[9] = execute(() -> {
            BatchRunner r = new BatchRunner();
            r.run(Arrays.asList(item("a", 1, false), item("b", 2, true)));
            return check("marked intact", r.done(1) && !r.done(2),
                "done(1)=" + r.done(1) + " done(2)=" + r.done(2));
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
