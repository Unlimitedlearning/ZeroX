public final class CacheService {
    private final LruCache cache;

    public CacheService(int capacity) {
        this.cache = new LruCache(capacity);
    }

    public Integer load(String key) {
        return cache.get(key);
    }

    public void store(String key, int value) {
        cache.put(key, value);
    }

    public boolean has(String key) {
        return cache.contains(key);
    }

    public int size() {
        return cache.size();
    }
}
