public final class SlotTable {
    private final Slot[] slots;

    public SlotTable(int capacity, String prefix) {
        this.slots = new Slot[capacity];
        for (int i = 0; i < capacity - 1; i++) {
            slots[i] = new Slot(i, prefix + i);
        }
    }

    public Slot get(int index) {
        if (index < 0 || index >= slots.length) {
            return null;
        }
        return slots[index];
    }

    public int size() {
        return slots.length;
    }
}
