/** 单调时钟：提供当前时间。 */
public class TimeSource {
    private long current;

    public TimeSource(long start) {
        this.current = start;
    }

    public long now() {
        return current;
    }

    public void advanceTo(long t) {
        if (t > current) {
            current = t;
        }
    }
}
