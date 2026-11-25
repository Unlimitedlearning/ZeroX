public class CircuitBreaker_405Test {

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

    static Breaker afterFirstRecovery() {
        Breaker b = new Breaker();
        CallService c = new CallService(b);
        c.call(false);
        c.call(false);
        c.call(false);
        b.cooldown();
        c.call(true);
        c.call(true);
        c.call(true);
        return b;
    }

    public static void main(String[] args) {
        String[] names = {
            "starts closed",
            "two failures keep it closed",
            "third failure opens circuit",
            "open circuit rejects calls",
            "cooldown enters half open",
            "one success stays half open",
            "three successes close circuit",
            "second round opens again",
            "half open count cleared on failure",
            "second cooldown enters half open",
            "second recovery needs three successes",
            "failure in half open reopens",
            "half open failure clears count before retry"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            Breaker b = new Breaker();
            return check("initial", b.state() == BreakerState.CLOSED && b.allow(),
                "state=" + b.state());
        });

        results[1] = execute(() -> {
            Breaker b = new Breaker();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            return check("two failures", b.state() == BreakerState.CLOSED && b.failures() == 2,
                "state=" + b.state() + " failures=" + b.failures());
        });

        results[2] = execute(() -> {
            Breaker b = new Breaker();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            return check("open", b.state() == BreakerState.OPEN, "state=" + b.state());
        });

        results[3] = execute(() -> {
            Breaker b = new Breaker();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            String r = c.call(true);
            return check("rejected", "rejected".equals(r) && !b.allow(), "got " + r);
        });

        results[4] = execute(() -> {
            Breaker b = new Breaker();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            b.cooldown();
            return check("half open", b.state() == BreakerState.HALF_OPEN && b.allow(),
                "state=" + b.state());
        });

        results[5] = execute(() -> {
            Breaker b = new Breaker();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            b.cooldown();
            c.call(true);
            return check("one success", b.state() == BreakerState.HALF_OPEN && b.halfSuccesses() == 1,
                "state=" + b.state() + " n=" + b.halfSuccesses());
        });

        results[6] = execute(() -> {
            Breaker b = afterFirstRecovery();
            return check("closed again", b.state() == BreakerState.CLOSED && b.failures() == 0,
                "state=" + b.state() + " failures=" + b.failures());
        });

        results[7] = execute(() -> {
            Breaker b = afterFirstRecovery();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            return check("second open", b.state() == BreakerState.OPEN, "state=" + b.state());
        });

        results[8] = execute(() -> {
            Breaker b = afterFirstRecovery();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            return check("count cleared", b.halfSuccesses() == 0,
                "halfSuccesses=" + b.halfSuccesses());
        });

        results[9] = execute(() -> {
            Breaker b = afterFirstRecovery();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            b.cooldown();
            return check("second half open", b.state() == BreakerState.HALF_OPEN,
                "state=" + b.state());
        });

        results[10] = execute(() -> {
            Breaker b = afterFirstRecovery();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            b.cooldown();
            c.call(true);
            return check("second recovery", b.state() == BreakerState.HALF_OPEN
                    && b.halfSuccesses() == 1,
                "state=" + b.state() + " n=" + b.halfSuccesses());
        });

        results[11] = execute(() -> {
            Breaker b = new Breaker();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            b.cooldown();
            c.call(false);
            return check("reopen", b.state() == BreakerState.OPEN, "state=" + b.state());
        });

        results[12] = execute(() -> {
            Breaker b = new Breaker();
            CallService c = new CallService(b);
            c.call(false);
            c.call(false);
            c.call(false);
            b.cooldown();
            c.call(true);
            c.call(false);
            b.cooldown();
            c.call(true);
            return check("retry after half-open failure",
                b.state() == BreakerState.HALF_OPEN && b.halfSuccesses() == 1,
                "state=" + b.state() + " n=" + b.halfSuccesses());
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
