import java.util.ArrayDeque;
import java.util.Deque;

public final class CycleGuard {
    private final Deque<String> active = new ArrayDeque<>();
    private int maxDepth;

    public boolean enter(String name) {
        if (active.contains(name)) {
            return false;
        }
        active.push(name);
        maxDepth = Math.max(maxDepth, active.size());
        return true;
    }

    public void leave(String name) {
        active.pop();
    }

    public int maxDepth() {
        return maxDepth;
    }
}
