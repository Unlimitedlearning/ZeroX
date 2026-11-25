import java.util.HashMap;
import java.util.Map;

public final class Inventory {
    private final Map<String, Integer> stock = new HashMap<>();

    public Inventory(String id, int amount) {
        stock.put(id, amount);
    }

    public void add(String id, int amount) {
        stock.put(id, amount);
    }

    public int get(String id) {
        Integer value = stock.get(id);
        return value == null ? 0 : value;
    }

    public void set(String id, int amount) {
        stock.put(id, amount);
    }
}
