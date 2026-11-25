public class ExprEval_305Test {

    interface Case {
        String run();
    }

    static String check(String label, String expr, int expected) {
        int actual = new CalcService().eval(expr);
        return actual == expected
                ? null
                : label + " -> " + expr + " expected " + expected + ", got " + actual;
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
            "single number",
            "multiply binds tighter than add",
            "minus is left associative",
            "power basic",
            "power is right associative",
            "parenthesized power stays left grouped",
            "power binds tighter than multiply",
            "power before trailing multiply",
            "division is left associative",
            "three-level right associativity",
            "three-base right associativity"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> check("single", "42", 42));
        results[1] = execute(() -> check("precedence", "2+3*4", 14));
        results[2] = execute(() -> check("left assoc", "10-2-3", 5));
        results[3] = execute(() -> check("power", "2^3", 8));
        results[4] = execute(() -> check("right assoc", "2^3^2", 512));
        results[5] = execute(() -> check("parenthesized", "(2^3)^2", 64));
        results[6] = execute(() -> check("mul before power", "2*3^2", 18));
        results[7] = execute(() -> check("power before mul", "2^3*2", 16));
        results[8] = execute(() -> check("division", "100/5/2", 10));
        results[9] = execute(() -> check("deep right assoc", "2^2^3^1", 256));
        results[10] = execute(() -> check("base three", "3^2^3", 6561));

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
