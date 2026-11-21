public final class Session {
    private final String id;
    private final long lastAccess;
    private final long createdAt;
    private final long maxInactiveMs;
    private final long maxLifetimeMs;

    public Session(String id, long now, long maxInactiveMs, long maxLifetimeMs) {
        this.id = id;
        this.lastAccess = now;
        this.createdAt = now;
        this.maxInactiveMs = maxInactiveMs;
        this.maxLifetimeMs = maxLifetimeMs;
    }

    public Session(String id, long lastAccess, long createdAt,
                   long maxInactiveMs, long maxLifetimeMs) {
        this.id = id;
        this.lastAccess = lastAccess;
        this.createdAt = createdAt;
        this.maxInactiveMs = maxInactiveMs;
        this.maxLifetimeMs = maxLifetimeMs;
    }

    public String getId() {
        return id;
    }

    public long getLastAccess() {
        return lastAccess;
    }

    public long getCreatedAt() {
        return createdAt;
    }

    public long getMaxInactiveMs() {
        return maxInactiveMs;
    }

    public long getMaxLifetimeMs() {
        return maxLifetimeMs;
    }

    public Session touch(long now) {
        return new Session(id, now, createdAt, maxInactiveMs, maxLifetimeMs);
    }

    public boolean isExpired(long now) {
        boolean idleExpired = (now - lastAccess) >= maxInactiveMs;
        boolean lifetimeExpired = (now - createdAt) >= maxLifetimeMs;
        return idleExpired && lifetimeExpired;
    }
}
