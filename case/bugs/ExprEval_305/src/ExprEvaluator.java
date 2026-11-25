import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

public final class ExprEvaluator {
    public int evaluate(String expr) {
        List<String> tokens = new Tokenizer().tokenize(expr);
        Deque<String> ops = new ArrayDeque<>();
        List<String> output = new ArrayList<>();

        for (String token : tokens) {
            if (isNumber(token)) {
                output.add(token);
            } else if ("(".equals(token)) {
                ops.push(token);
            } else if (")".equals(token)) {
                while (!ops.isEmpty() && !"(".equals(ops.peek())) {
                    output.add(ops.pop());
                }
                if (!ops.isEmpty()) {
                    ops.pop();
                }
            } else {
                while (!ops.isEmpty() && isOperator(ops.peek())
                        && (prec(ops.peek()) > prec(token)
                            || prec(ops.peek()) == prec(token))) {
                    output.add(ops.pop());
                }
                ops.push(token);
            }
        }
        while (!ops.isEmpty()) {
            output.add(ops.pop());
        }

        Deque<Integer> values = new ArrayDeque<>();
        for (String token : output) {
            if (isNumber(token)) {
                values.push(Integer.parseInt(token));
            } else {
                int b = values.pop();
                int a = values.pop();
                values.push(apply(a, b, token));
            }
        }
        return values.pop();
    }

    private static boolean isNumber(String token) {
        return !token.isEmpty() && Character.isDigit(token.charAt(0));
    }

    private static boolean isOperator(String token) {
        return "+".equals(token) || "-".equals(token) || "*".equals(token)
                || "/".equals(token) || "^".equals(token);
    }

    private static int prec(String op) {
        if ("+".equals(op) || "-".equals(op)) {
            return 1;
        }
        if ("*".equals(op) || "/".equals(op)) {
            return 2;
        }
        return 3;
    }

    private static int apply(int a, int b, String op) {
        if ("+".equals(op)) {
            return a + b;
        }
        if ("-".equals(op)) {
            return a - b;
        }
        if ("*".equals(op)) {
            return a * b;
        }
        if ("/".equals(op)) {
            return a / b;
        }
        return (int) Math.pow(a, b);
    }
}
