import java.util.Deque;
import java.util.LinkedList;

public class RateWindow {
    private long lastAccess;
    private long maxInactiveMs;
    private Deque<Long> accessTimes;

    public RateWindow(long maxInactiveMs) {
        this.lastAccess = System.currentTimeMillis();
        this.maxInactiveMs = maxInactiveMs;
        this.accessTimes = new LinkedList<>();
    }

    public long getLastAccess() {
        return lastAccess;
    }

    public long getMaxInactiveMs() {
        return maxInactiveMs;
    }

    public void setLastAccess(long lastAccess) {
        this.lastAccess = lastAccess;
    }

    public void setMaxInactiveMs(long maxInactiveMs) {
        this.maxInactiveMs = maxInactiveMs;
    }

    public Deque<Long> getAccessTimes() {
        return accessTimes;
    }

    public void setAccessTimes(Deque<Long> accessTimes) {
        this.accessTimes = accessTimes;
    }

    public void touch(long now) {
        this.lastAccess = now;
        this.accessTimes.addLast(now);
        purge(now);
    }

    public void purge(long now) {
        long threshold = now - maxInactiveMs;
        while (!accessTimes.isEmpty() && accessTimes.peekFirst() <= threshold) {
            accessTimes.pollFirst();
        }
    }

    public int size() {
        return accessTimes.size();
    }

    public boolean isExpired(long now) {
        return now - lastAccess > maxInactiveMs;
    }

    public long getLimit() {
        return 100L;
    }

    public boolean allowRequest(long now) {
        if (isExpired(now)) {
            touch(now);
        }
        if (size() >= getLimit()) {
            return false;
        }
        accessTimes.addLast(now);
        return true;
    }
}
