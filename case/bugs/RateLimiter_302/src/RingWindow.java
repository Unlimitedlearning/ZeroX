import java.util.Arrays;

/** 定长环形时间戳窗口。满之前从槽 0 起顺序写；满之后覆盖最旧的槽。 */
public class RingWindow {
    private final long[] stamps;
    private final int capacity;
    private int cursor;
    private int filled;

    public RingWindow(int capacity) {
        this.capacity = capacity;
        this.stamps = new long[capacity];
    }

    public void record(long now) {
        stamps[cursor] = now;
        cursor = (cursor + 1) % capacity;
        if (filled < capacity) {
            filled++;
        }
    }

    /** 窗口内最旧的时间戳。 */
    public long oldest() {
        return stamps[0];
    }

    /** 严格晚于 t 的条目数。 */
    public int countAfter(long t) {
        int n = 0;
        for (int i = 0; i < filled; i++) {
            if (stamps[i] > t) {
                n++;
            }
        }
        return n;
    }

    public int filled() {
        return filled;
    }

    public int cursor() {
        return cursor;
    }

    public long slot(int i) {
        return stamps[i];
    }

    public long[] snapshot() {
        return Arrays.copyOf(stamps, capacity);
    }
}
