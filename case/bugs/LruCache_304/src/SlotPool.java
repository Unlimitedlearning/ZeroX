/** Fixed-size ring of slots with moveToHead / removeLast. */
public final class SlotPool {
    private final String[] slots;
    private int cursor;

    public SlotPool(int capacity) {
        this.slots = new String[capacity];
    }

    public void put(int index, String value) {
        slots[index % slots.length] = value;
    }

    public String get(int index) {
        return slots[index % slots.length];
    }

    public void moveToHead(int index) {
        cursor = index % slots.length;
    }

    public int removeLast() {
        int at = cursor;
        cursor = (cursor + 1) % slots.length;
        return at;
    }
}
