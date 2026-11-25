import java.util.HashMap;
import java.util.Map;

public final class LruCache {
    private final int capacity;
    private final DList list = new DList();
    private final Map<String, LruNode> index = new HashMap<>();

    public LruCache(int capacity) {
        this.capacity = capacity;
    }

    public Integer get(String key) {
        LruNode node = index.get(key);
        if (node == null) {
            return null;
        }
        list.moveToHead(node);
        return node.value;
    }

    public void put(String key, int value) {
        LruNode existing = index.get(key);
        if (existing != null) {
            existing.value = value;
            list.moveToHead(existing);
            return;
        }
        LruNode fresh = new LruNode(key, value);
        list.addFirst(fresh);
        index.put(key, fresh);
        if (list.size() > capacity) {
            LruNode victim = list.removeLast();
            index.remove(victim.key);
        }
    }

    public boolean contains(String key) {
        return index.containsKey(key);
    }

    public int size() {
        return index.size();
    }
}
