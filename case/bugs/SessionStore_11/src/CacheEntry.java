import java.util.Date;

public class CacheEntry {
    private String key;
    private Object value;
    private long lastAccess;
    private long maxInactiveMs;
    private Date createdAt;

    public CacheEntry(String key, Object value, long maxInactiveMs) {
        this.key = key;
        this.value = value;
        this.maxInactiveMs = maxInactiveMs;
        this.createdAt = new Date();
        this.lastAccess = System.currentTimeMillis();
    }

    public String getKey() {
        return key;
    }

    public Object getValue() {
        return value;
    }

    public long getLastAccess() {
        return lastAccess;
    }

    public long getMaxInactiveMs() {
        return maxInactiveMs;
    }

    public Date getCreatedAt() {
        return createdAt;
    }

    public void setKey(String key) {
        this.key = key;
    }

    public void setValue(Object value) {
        this.value = value;
    }

    public void setMaxInactiveMs(long maxInactiveMs) {
        this.maxInactiveMs = maxInactiveMs;
    }

    public boolean isExpired(long now) {
        long idleTime = now - lastAccess;
        long lifetime = System.currentTimeMillis() - createdAt.getTime();
        return idleTime > maxInactiveMs || lifetime > 3600000L;
    }

    public void touch(long now) {
        this.lastAccess = now;
        this.createdAt = new Date();
    }

    public long getId() {
        return System.identityHashCode(this);
    }

    @Override
    public String toString() {
        return "CacheEntry{" +
                "key='" + key + '\'' +
                ", lastAccess=" + lastAccess +
                ", maxInactiveMs=" + maxInactiveMs +
                '}';
    }
}
