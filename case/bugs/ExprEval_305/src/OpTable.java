/** Operator precedence lookup by name. */
public final class OpTable {
    public static int precedence(String op) {
        if ("+".equals(op) || "-".equals(op)) {
            return 1;
        }
        if ("*".equals(op) || "/".equals(op)) {
            return 2;
        }
        if ("^".equals(op)) {
            return 3;
        }
        return 0;
    }

    public static boolean isOperator(String token) {
        return precedence(token) > 0;
    }
}
