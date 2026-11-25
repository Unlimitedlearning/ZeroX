import java.util.ArrayDeque;
import java.util.Deque;

public final class CursorWindow {
    private final Deque<Long> marks = new ArrayDeque<>();
    private long highWater;

    public void mark(long id) {
        marks.addLast(id);
        highWater = Math.max(highWater, id);
    }

    public long oldest() {
        return marks.isEmpty() ? -1 : marks.peekFirst();
    }

    public long highWater() {
        return highWater;
    }
}
