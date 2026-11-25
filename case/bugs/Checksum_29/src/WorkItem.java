public final class WorkItem {
    private final String name;
    private final int bit;
    private final boolean fails;

    public WorkItem(String name, int bit, boolean fails) {
        this.name = name;
        this.bit = bit;
        this.fails = fails;
    }

    public String name() {
        return name;
    }

    public int bit() {
        return bit;
    }

    public boolean shouldFail() {
        return fails;
    }
}
