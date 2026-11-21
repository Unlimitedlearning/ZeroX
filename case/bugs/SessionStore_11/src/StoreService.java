public final class StoreService {
    private final SessionStore store;
    private final long maxInactiveMs;
    private final long maxLifetimeMs;

    public StoreService(long maxInactiveMs, long maxLifetimeMs) {
        this.store = new SessionStore();
        this.maxInactiveMs = maxInactiveMs;
        this.maxLifetimeMs = maxLifetimeMs;
    }

    public void create(String id, long now) {
        store.create(id, now, maxInactiveMs, maxLifetimeMs);
    }

    public Session get(String id, long now) {
        Session s = store.lookup(id);
        if (s == null || s.isExpired(now)) {
            return null;
        }
        return s;
    }

    public boolean touch(String id, long now) {
        Session s = store.lookup(id);
        if (s == null) {
            return false;
        }
        store.touch(id, now);
        return true;
    }

    public int purge(long now) {
        return store.purge(now);
    }

    public int size() {
        return store.size();
    }
}
