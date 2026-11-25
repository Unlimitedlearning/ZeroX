import java.util.ArrayDeque;
import java.util.Deque;

public final class WindowCounter {
    private final Deque<Long> events = new ArrayDeque<>();

    public void record(long at) {
        events.addLast(at);
    }

    public int count(long since) {
        int n = 0;
        for (long at : events) {
            if (at >= since) {
                n++;
            }
        }
        return n;
    }
}
