import java.util.Arrays;
import java.util.List;

public class PriorityBatch_404Test {

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

    static Job j(String id, int priority, int sequence) {
        return new Job(id, priority, sequence);
    }

    static DispatchService svc() {
        return new DispatchService(new JobQueue());
    }

    public static void main(String[] args) {
        String[] names = {
            "single job dispatched",
            "empty queue dispatches nothing",
            "distinct priorities ordered",
            "equal priority keeps arrival order",
            "mixed priorities full order",
            "all jobs dispatched exactly once",
            "control queue keeps arrival order",
            "later high priority jumps ahead",
            "equal priority across add batches",
            "repeated ids are not collapsed",
            "ten equal priority jobs ordered"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            List<String> out = svc().dispatch(j("a", 2, 0));
            return check("single", out.equals(Arrays.asList("a")), "got " + out);
        });

        results[1] = execute(() -> {
            List<String> out = svc().dispatch();
            return check("empty", out.isEmpty(), "got " + out);
        });

        results[2] = execute(() -> {
            List<String> out = svc().dispatch(j("a", 2, 0), j("b", 1, 1), j("c", 3, 2));
            return check("priorities", out.equals(Arrays.asList("b", "a", "c")), "got " + out);
        });

        results[3] = execute(() -> {
            List<String> out = svc().dispatch(
                j("n0", 2, 0), j("n1", 2, 1), j("n2", 2, 2),
                j("n3", 2, 3), j("n4", 2, 4), j("n5", 2, 5));
            List<String> want = Arrays.asList("n0", "n1", "n2", "n3", "n4", "n5");
            return check("equal priority", out.equals(want), "got " + out);
        });

        results[4] = execute(() -> {
            List<String> out = svc().dispatch(
                j("a", 2, 0), j("b", 1, 1), j("c", 2, 2),
                j("d", 1, 3), j("e", 3, 4));
            return check("mixed", out.equals(Arrays.asList("b", "d", "a", "c", "e")), "got " + out);
        });

        results[5] = execute(() -> {
            List<String> out = svc().dispatch(
                j("a", 2, 0), j("b", 2, 1), j("c", 2, 2));
            long distinct = out.stream().distinct().count();
            return check("exactly once", out.size() == 3 && distinct == 3, "got " + out);
        });

        results[6] = execute(() -> {
            RankQueue q = new RankQueue();
            q.add(j("r0", 2, 0));
            q.add(j("r1", 2, 1));
            q.add(j("r2", 2, 2));
            List<String> out = Arrays.asList(q.next(), q.next(), q.next());
            return check("control", out.equals(Arrays.asList("r0", "r1", "r2")), "got " + out);
        });

        results[7] = execute(() -> {
            List<String> out = svc().dispatch(j("a", 2, 0), j("b", 2, 1), j("c", 1, 2));
            return check("late high", out.equals(Arrays.asList("c", "a", "b")), "got " + out);
        });

        results[8] = execute(() -> {
            JobQueue q = new JobQueue();
            BatchRunner r = new BatchRunner(q);
            r.add(j("a", 2, 0));
            r.add(j("b", 2, 1));
            List<String> out = r.drain();
            return check("two batches", out.equals(Arrays.asList("a", "b")), "got " + out);
        });

        results[9] = execute(() -> {
            List<String> out = svc().dispatch(j("same", 1, 0), j("same", 1, 1));
            return check("repeated id", out.size() == 2, "got " + out);
        });

        results[10] = execute(() -> {
            Job[] jobs = new Job[10];
            String[] want = new String[10];
            for (int i = 0; i < 10; i++) {
                jobs[i] = j("k" + i, 4, i);
                want[i] = "k" + i;
            }
            List<String> out = svc().dispatch(jobs);
            return check("ten equal", out.equals(Arrays.asList(want)), "got " + out);
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
