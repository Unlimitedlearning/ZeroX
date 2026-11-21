import java.util.HashMap;
import java.util.Map;

public class LabelRegistry {
    private Map<Integer, String> registry;
    private int nextId;

    public LabelRegistry() {
        this.registry = new HashMap<>();
        this.nextId = 0;
    }

    public String get(int index) {
        return registry.get(index);
    }

    public String label(int index) {
        return registry.getOrDefault(index, "Unknown");
    }

    public void register(int index, String text) {
        if (index < 0) {
            throw new IllegalArgumentException("Index cannot be negative");
        }
        registry.put(index, text);
    }

    public int size() {
        return registry.size();
    }

    public boolean containsKey(int index) {
        return registry.containsKey(index);
    }

    public void unregister(int index) {
        registry.remove(index);
    }

    public String getLabelText(int index) {
        return label(index);
    }
}
