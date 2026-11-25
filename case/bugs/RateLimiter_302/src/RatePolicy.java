/** 静态阈值策略：容量与窗口的默认值及取整。 */
public class RatePolicy {
    public static final int DEFAULT_CAPACITY = 4;
    public static final long DEFAULT_WINDOW_MS = 1000L;

    public static int clampCapacity(int n) {
        return n < 1 ? 1 : n;
    }

    public static long clampWindow(long ms) {
        return ms < 1 ? 1 : ms;
    }
}
