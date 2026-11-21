import java.util.Arrays;

public class SlotCache {
    private String[] cache;
    private int capacity;
    private long lastAccess = 0L;

    public SlotCache(int size) {
        this.capacity = size;
        this.cache = new String[capacity];
    }

    public String get(int index) {
        if (index < 0 || index >= cache.length) {
            return null;
        }
        lastAccess = System.currentTimeMillis();
        return cache[index];
    }

    public void put(int index, String value) {
        if (index < 0 || index >= cache.length) {
            throw new IndexOutOfBoundsException("Index out of bounds");
        }
        cache[index] = value;
    }

    public int size() {
        return (int) Arrays.stream(cache).filter(s -> s != null).count();
    }

    public void clear() {
        Arrays.fill(cache, null);
    }

    public long getLastAccessTime() {
        return lastAccess;
    }

    public boolean contains(String value) {
        for (String item : cache) {
            if (value.equals(item)) {
                return true;
            }
        }
        return false;
    }
}
