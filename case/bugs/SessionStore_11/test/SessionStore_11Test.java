public class SessionStore_11Test {

    private static final long MAX_IDLE = 100L;
    private static final long MAX_LIFETIME = 250L;

    interface Case {
        String run();
    }

    static String check(String label, boolean ok, String detail) {
        return ok ? null : label + " -> " + detail;
    }

    static String first(String... diffs) {
        for (String d : diffs) {
            if (d != null) {
                return d;
            }
        }
        return null;
    }

    static String execute(Case c) {
        try {
            return c.run();
        } catch (Throwable t) {
            return t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    public static void main(String[] args) {
        String[] names = {
            "create then immediate get",
            "alive before idle boundary",
            "expire at idle boundary",
            "expire when idle elapses before lifetime",
            "expire when both clocks elapse",
            "touch keeps session fresh",
            "expire after touch via idle",
            "lifetime expires despite touch",
            "purge removes idle-expired entries",
            "size after purge"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            StoreService svc = new StoreService(MAX_IDLE, MAX_LIFETIME);
            svc.create("a", 0L);
            Session s = svc.get("a", 0L);
            return check("get immediately", s != null, "expected alive, got " + (s == null ? "null" : "session"));
        });

        results[1] = execute(() -> {
            StoreService svc = new StoreService(MAX_IDLE, MAX_LIFETIME);
            svc.create("b", 0L);
            Session s = svc.get("b", 99L);
            return check("get before idle boundary", s != null, "expected alive, got " + (s == null ? "null" : "session"));
        });

        results[2] = execute(() -> {
            StoreService svc = new StoreService(MAX_IDLE, MAX_LIFETIME);
            svc.create("c", 0L);
            Session s = svc.get("c", 100L);
            return check("expire at idle boundary", s == null, "expected null at now-last==maxInactive, got " + (s == null ? "null" : "session"));
        });

        results[3] = execute(() -> {
            StoreService svc = new StoreService(MAX_IDLE, MAX_LIFETIME);
            svc.create("d", 0L);
            Session s = svc.get("d", 150L);
            return check("expire via idle only", s == null, "expected null when idle elapsed but lifetime not, got " + (s == null ? "null" : "session"));
        });

        results[4] = execute(() -> {
            StoreService svc = new StoreService(MAX_IDLE, MAX_LIFETIME);
            svc.create("e", 0L);
            Session s = svc.get("e", 250L);
            return check("expire via both clocks", s == null, "expected null when both clocks elapsed, got " + (s == null ? "null" : "session"));
        });

        results[5] = execute(() -> {
            StoreService svc = new StoreService(MAX_IDLE, MAX_LIFETIME);
            svc.create("f", 0L);
            boolean touched = svc.touch("f", 60L);
            Session s = svc.get("f", 130L);
            return first(
                check("touch returns true", touched, "expected true, got " + touched),
                check("touch keeps fresh", s != null, "expected alive after touch, got " + (s == null ? "null" : "session")));
        });

        results[6] = execute(() -> {
            StoreService svc = new StoreService(MAX_IDLE, MAX_LIFETIME);
            svc.create("g", 0L);
            svc.touch("g", 60L);
            Session s = svc.get("g", 200L);
            return check("expire after touch via idle", s == null, "expected null when idle elapsed after touch, got " + (s == null ? "null" : "session"));
        });

        results[7] = execute(() -> {
            StoreService svc = new StoreService(MAX_IDLE, MAX_LIFETIME);
            svc.create("h", 0L);
            svc.touch("h", 200L);
            Session s = svc.get("h", 280L);
            return check("lifetime expires despite touch", s == null, "expected null when lifetime elapsed even after touch, got " + (s == null ? "null" : "session"));
        });

        StoreService purgeSvc = new StoreService(MAX_IDLE, MAX_LIFETIME);
        results[8] = execute(() -> {
            purgeSvc.create("i", 0L);
            purgeSvc.create("j", 50L);
            int removed = purgeSvc.purge(150L);
            return check("purge removes idle-expired", removed == 2, "expected 2, got " + removed);
        });

        results[9] = execute(() -> {
            int size = purgeSvc.size();
            return check("size after purge", size == 0, "expected 0, got " + size);
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
