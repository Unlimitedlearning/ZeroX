public final class RenderService {
    private final Resolver resolver;
    private final int capacity;

    public RenderService(int capacity, String prefix) {
        this.resolver = new Resolver(new SlotTable(capacity, prefix));
        this.capacity = capacity;
    }

    public String render(int index) {
        return "[" + resolver.labelAt(index) + "]";
    }

    public String describe(int index) {
        return resolver.describeAt(index);
    }

    public int capacity() {
        return capacity;
    }
}
