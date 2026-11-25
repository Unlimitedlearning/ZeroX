import java.util.ArrayDeque;
import java.util.Deque;

/** Operand stack with push / pop / apply. */
public final class NumberStack {
    private final Deque<Long> values = new ArrayDeque<>();

    public void push(long value) {
        values.push(value);
    }

    public long pop() {
        return values.pop();
    }

    public long apply(String op) {
        long b = values.pop();
        long a = values.pop();
        long result;
        if ("+".equals(op)) {
            result = a + b;
        } else if ("*".equals(op)) {
            result = a * b;
        } else {
            result = a - b;
        }
        values.push(result);
        return result;
    }
}
