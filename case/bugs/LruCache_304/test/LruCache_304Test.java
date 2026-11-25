public class LruCache_304Test {

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

    static CacheService fresh() {
        CacheService s = new CacheService(3);
        s.store("a", 1);
        s.store("b", 2);
        s.store("c", 3);
        return s;
    }

    public static void main(String[] args) {
        String[] names = {
            "three stores fill cache",
            "get existing returns value",
            "get missing returns null",
            "middle hit then insert evicts tail",
            "tail hit then insert evicts new tail",
            "tail hit survives two later inserts",
            "update existing keeps capacity",
            "fourth insert evicts exactly one",
            "repeated tail hits keep key alive",
            "tail hit after update stays alive"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> check("size", fresh().size() == 3, "expected 3"));

        results[1] = execute(() -> {
            Integer v = fresh().load("a");
            return check("value", Integer.valueOf(1).equals(v), "expected 1, got " + v);
        });

        results[2] = execute(() -> {
            Integer v = fresh().load("z");
            return check("missing", v == null, "expected null, got " + v);
        });

        results[3] = execute(() -> {
            CacheService s = fresh();
            s.load("b");
            s.store("d", 4);
            return check("middle hit", !s.has("a") && s.has("b") && s.has("c") && s.has("d"),
                "expected a evicted, b/c/d alive; b=" + s.has("b") + " c=" + s.has("c") + " d=" + s.has("d"));
        });

        results[4] = execute(() -> {
            CacheService s = fresh();
            s.load("a");
            s.store("d", 4);
            return check("tail hit", !s.has("b") && s.has("a") && s.has("c") && s.has("d"),
                "expected b evicted, a/c/d alive; a=" + s.has("a") + " b=" + s.has("b"));
        });

        results[5] = execute(() -> {
            CacheService s = fresh();
            s.load("a");
            s.store("d", 4);
            s.store("e", 5);
            return check("tail hit then two inserts", s.has("a"), "expected a alive after two inserts");
        });

        results[6] = execute(() -> {
            CacheService s = fresh();
            s.store("a", 99);
            Integer v = s.load("a");
            return check("update", s.size() == 3 && Integer.valueOf(99).equals(v),
                "size=" + s.size() + " value=" + v);
        });

        results[7] = execute(() -> {
            CacheService s = fresh();
            s.store("d", 4);
            return check("fourth insert", s.size() == 3 && !s.has("a"), "size=" + s.size() + " a=" + s.has("a"));
        });

        results[8] = execute(() -> {
            CacheService s = fresh();
            s.load("a");
            s.load("a");
            s.store("d", 4);
            return check("repeated tail hit", s.has("a"), "expected a alive");
        });

        results[9] = execute(() -> {
            CacheService s = fresh();
            s.store("a", 9);
            s.load("b");
            s.load("a");
            s.store("d", 4);
            return check("tail hit after update", s.has("a"), "expected a alive");
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
