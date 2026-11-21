public final class Slot {
    private final int index;
    private final String label;

    public Slot(int index, String label) {
        this.index = index;
        this.label = label;
    }

    public int getIndex() {
        return index;
    }

    public String label() {
        return label;
    }

    public String describe() {
        return index + ":" + label;
    }
}
