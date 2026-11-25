public class RateLimiter_302Test {

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

    public static void main(String[] args) {
        String[] names = {
            "first request accepted",
            "fills up to capacity",
            "rejects within window",
            "accepts after window slides",
            "rejection does not consume slot",
            "window ordered before wrap",
            "window ordered after wrap",
            "wrap keeps limiter accepting",
            "many wraps stay stable",
            "countAfter counts strictly later",
            "snapshot shows ring content",
            "time source is monotonic",
            "policy clamps are independent"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            SlidingLimiter lim = new SlidingLimiter(4, 100);
            return check("accepted", lim.allow(0), "rejected first request");
        });

        results[1] = execute(() -> {
            SlidingLimiter lim = new SlidingLimiter(4, 100);
            for (int t = 0; t < 4; t++) {
                lim.allow(t);
            }
            return check("filled", lim.accepted() == 4, "accepted=" + lim.accepted());
        });

        results[2] = execute(() -> {
            SlidingLimiter lim = new SlidingLimiter(4, 100);
            for (int t = 0; t < 4; t++) {
                lim.allow(t);
            }
            return check("rejected", !lim.allow(50), "accepted within window");
        });

        results[3] = execute(() -> {
            SlidingLimiter lim = new SlidingLimiter(4, 100);
            for (int t = 0; t < 4; t++) {
                lim.allow(t);
            }
            return check("accepted", lim.allow(100), "rejected after window slid");
        });

        results[4] = execute(() -> {
            SlidingLimiter lim = new SlidingLimiter(4, 100);
            for (int t = 0; t < 4; t++) {
                lim.allow(t);
            }
            lim.allow(50);
            return check("no consume", lim.accepted() == 4, "accepted=" + lim.accepted());
        });

        results[5] = execute(() -> {
            RingWindow w = new RingWindow(4);
            w.record(10);
            w.record(20);
            w.record(30);
            w.record(40);
            return check("oldest", w.oldest() == 10, "oldest=" + w.oldest());
        });

        results[6] = execute(() -> {
            RingWindow w = new RingWindow(4);
            w.record(10);
            w.record(20);
            w.record(30);
            w.record(40);
            w.record(50);
            return check("oldest after wrap", w.oldest() == 20, "oldest=" + w.oldest());
        });

        results[7] = execute(() -> {
            SlidingLimiter lim = new SlidingLimiter(4, 100);
            for (int t = 0; t < 4; t++) {
                lim.allow(t);
            }
            lim.allow(100);
            return check("accept after wrap", lim.allow(101), "rejected at 101");
        });

        results[8] = execute(() -> {
            SlidingLimiter lim = new SlidingLimiter(4, 100);
            long t = 0;
            int accepted = 0;
            for (int k = 0; k < 12; k++) {
                if (lim.allow(t)) {
                    accepted++;
                }
                t += 100;
            }
            return check("stable", accepted == 12, "accepted=" + accepted);
        });

        results[9] = execute(() -> {
            RingWindow w = new RingWindow(4);
            w.record(10);
            w.record(20);
            w.record(30);
            return check("countAfter", w.countAfter(20) == 1 && w.countAfter(10) == 2,
                "c20=" + w.countAfter(20) + " c10=" + w.countAfter(10));
        });

        results[10] = execute(() -> {
            RingWindow w = new RingWindow(4);
            w.record(10);
            w.record(20);
            w.record(30);
            w.record(40);
            w.record(50);
            long[] s = w.snapshot();
            return check("snapshot", s[0] == 50 && s[1] == 20 && w.cursor() == 1,
                "s0=" + s[0] + " s1=" + s[1] + " cursor=" + w.cursor());
        });

        results[11] = execute(() -> {
            TimeSource ts = new TimeSource(5);
            ts.advanceTo(3);
            ts.advanceTo(9);
            return check("monotonic", ts.now() == 9, "now=" + ts.now());
        });

        results[12] = execute(() -> {
            return check("clamps", RatePolicy.clampCapacity(0) == 1
                    && RatePolicy.clampWindow(0) == 1, "policy mismatch");
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
