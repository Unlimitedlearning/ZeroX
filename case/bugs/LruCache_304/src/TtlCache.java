import java.util.HashMap;
import java.util.Map;

/** TTL cache: get / put / capacity with time-based eviction. */
public final class TtlCache {
    private final Map<String, Long> expires = new HashMap<>();
    private final int capacity;

    public TtlCache(int capacity) {
        this.capacity = capacity;
    }

    public Integer get(String key) {
        Long at = expires.get(key);
        if (at == null || at < System.currentTimeMillis()) {
            return null;
        }
        return (int) (long) at;
    }

    public void put(String key, int value) {
        if (expires.size() >= capacity) {
            expires.clear();
        }
        expires.put(key, value + System.currentTimeMillis());
    }
}
