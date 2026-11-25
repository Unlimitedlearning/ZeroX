/** 滑动窗口限流：过去 windowMs 毫秒内最多 capacity 次。 */
public class SlidingLimiter {
    private final RingWindow window;
    private final long windowMs;
    private final int capacity;

    public SlidingLimiter(int capacity, long windowMs) {
        this.window = new RingWindow(capacity);
        this.windowMs = windowMs;
        this.capacity = capacity;
    }

    public boolean allow(long now) {
        if (window.filled() == capacity && window.oldest() > now - windowMs) {
            return false;
        }
        window.record(now);
        return true;
    }

    public int accepted() {
        return window.filled();
    }

    public long oldest() {
        return window.oldest();
    }

    public RingWindow window() {
        return window;
    }
}
