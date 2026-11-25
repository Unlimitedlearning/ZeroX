public class ShardRouter_408Test {

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

    static ShardRouter fresh() {
        return new ShardRouter();
    }

    public static void main(String[] args) {
        String[] names = {
            "initial virtual points",
            "all nodes present",
            "route returns known node",
            "remove clears declared replica",
            "remove clears node presence",
            "remove clears all virtual points",
            "removed node never routed",
            "remaining routes still work",
            "remove second node",
            "readd restores points",
            "hash is deterministic",
            "key beyond ring wraps around",
            "metrics rate independent"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            ShardRouter r = fresh();
            return check("points", r.points() == 9, "points=" + r.points());
        });

        results[1] = execute(() -> {
            ShardRouter r = fresh();
            return check("present", r.has("alpha") && r.has("beta") && r.has("gamma"), "missing");
        });

        results[2] = execute(() -> {
            ShardRouter r = fresh();
            String n = r.route(42);
            return check("known", r.has(n), "routed=" + n);
        });

        results[3] = execute(() -> {
            ShardRouter r = fresh();
            r.remove("alpha");
            return check("declared", r.declaredReplicas("alpha") == 0,
                "declared=" + r.declaredReplicas("alpha"));
        });

        results[4] = execute(() -> {
            ShardRouter r = fresh();
            r.remove("alpha");
            return check("presence", !r.has("alpha"), "alpha still on ring");
        });

        results[5] = execute(() -> {
            ShardRouter r = fresh();
            r.remove("alpha");
            return check("point count", r.points() == 6, "points=" + r.points());
        });

        results[6] = execute(() -> {
            ShardRouter r = fresh();
            r.remove("alpha");
            for (int k = 0; k < 200; k++) {
                if ("alpha".equals(r.route(k))) {
                    return check("not routed", false, "alpha at key " + k);
                }
            }
            return null;
        });

        results[7] = execute(() -> {
            ShardRouter r = fresh();
            r.remove("alpha");
            String n = r.route(42);
            return check("remaining", r.has(n), "routed=" + n);
        });

        results[8] = execute(() -> {
            ShardRouter r = fresh();
            r.remove("alpha");
            r.remove("beta");
            return check("two removed", r.points() == 3 && !r.has("beta"),
                "points=" + r.points() + " beta=" + r.has("beta"));
        });

        results[9] = execute(() -> {
            ShardRouter r = fresh();
            r.remove("alpha");
            r.add("alpha", 3);
            return check("readd", r.points() == 9 && r.has("alpha"), "points=" + r.points());
        });

        results[10] = execute(() -> {
            return check("hash", HashRing.hash("x") == HashRing.hash("x")
                    && KeyHasher.of("k") == KeyHasher.of("k"), "nondeterministic");
        });

        results[12] = execute(() -> {
            ShardRouter r = fresh();
            String n = r.route(Integer.MAX_VALUE);
            return check("wrap", "beta".equals(n), "routed=" + n);
        });

        results[11] = execute(() -> {
            RingMetrics m = new RingMetrics();
            m.routedOne();
            m.missedOne();
            return check("metrics", m.hitRate() == 0.5, "rate=" + m.hitRate());
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
