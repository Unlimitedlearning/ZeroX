public final class TaskMask {
    private int mask;

    public boolean has(int bit) {
        return (mask & bit) != 0;
    }

    public void mark(int bit) {
        mask |= bit;
    }

    public void clear(int bit) {
        mask ^= bit;
    }

    public int value() {
        return mask;
    }
}
