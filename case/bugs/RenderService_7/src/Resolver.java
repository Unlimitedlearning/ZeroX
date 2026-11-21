public final class Resolver {
    private final SlotTable table;

    public Resolver(SlotTable table) {
        this.table = table;
    }

    public String labelAt(int index) {
        return table.get(index).label();
    }

    public String describeAt(int index) {
        return table.get(index).describe();
    }
}
