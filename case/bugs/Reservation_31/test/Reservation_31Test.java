public class Reservation_31Test {

    interface Case {
        String run();
    }

    static ReservationService svc() {
        return new ReservationService(new Inventory("a", 10));
    }

    static String check(String label, boolean ok, String detail) {
        return ok ? null : label + " -> " + detail;
    }

    static String execute(Case c) {
        try {
            return c.run();
        } catch (Throwable t) {
            return "UNEXPECTED " + t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    public static void main(String[] args) {
        String[] names = {
            "first reservation returns remainder",
            "accepted counter increments",
            "quota rejection throws",
            "quota rejection leaves stock intact",
            "retry after quota rejection succeeds",
            "stock rejection throws",
            "stock rejection leaves stock intact",
            "cumulative reservations",
            "quota boundary is inclusive",
            "different ids are independent"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            int left = svc().reserve("a", 2, 5);
            return check("remainder", left == 8, "expected 8, got " + left);
        });

        results[1] = execute(() -> {
            ReservationService s = svc();
            s.reserve("a", 1, 5);
            return check("counter", s.accepted() == 1, "expected 1, got " + s.accepted());
        });

        results[2] = execute(() -> {
            try {
                svc().reserve("a", 9, 5);
                return check("throws", false, "no exception");
            } catch (IllegalArgumentException expected) {
                return null;
            }
        });

        results[3] = execute(() -> {
            ReservationService s = svc();
            try {
                s.reserve("a", 9, 5);
            } catch (IllegalArgumentException expected) {
                // expected
            }
            return check("stock intact", s.stockOf("a") == 10, "expected 10, got " + s.stockOf("a"));
        });

        results[4] = execute(() -> {
            ReservationService s = svc();
            try {
                s.reserve("a", 9, 5);
            } catch (IllegalArgumentException expected) {
                // expected
            }
            int left = s.reserve("a", 2, 5);
            return check("retry", left == 8, "expected 8, got " + left);
        });

        results[5] = execute(() -> {
            try {
                svc().reserve("a", 20, 50);
                return check("throws", false, "no exception");
            } catch (IllegalStateException expected) {
                return null;
            }
        });

        results[6] = execute(() -> {
            ReservationService s = svc();
            try {
                s.reserve("a", 20, 50);
            } catch (IllegalStateException expected) {
                // expected
            }
            return check("stock intact", s.stockOf("a") == 10, "expected 10, got " + s.stockOf("a"));
        });

        results[7] = execute(() -> {
            ReservationService s = svc();
            s.reserve("a", 2, 5);
            int left = s.reserve("a", 3, 5);
            return check("cumulative", left == 5 && s.stockOf("a") == 5, "left=" + left + " stock=" + s.stockOf("a"));
        });

        results[8] = execute(() -> {
            int left = svc().reserve("a", 5, 5);
            return check("boundary", left == 5, "expected 5, got " + left);
        });

        results[9] = execute(() -> {
            Inventory inv = new Inventory("a", 10);
            inv.add("b", 10);
            ReservationService s = new ReservationService(inv);
            s.reserve("a", 2, 5);
            s.reserve("b", 3, 5);
            return check("independent", s.stockOf("a") == 8 && s.stockOf("b") == 7,
                "a=" + s.stockOf("a") + " b=" + s.stockOf("b"));
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
