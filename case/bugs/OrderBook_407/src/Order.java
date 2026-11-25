public class Order {
    public enum Side { BUY, SELL }

    private final String id;
    private final Side side;
    private final int price;
    private final int originalQty;
    private int remaining;

    public Order(String id, Side side, int price, int qty) {
        this.id = id;
        this.side = side;
        this.price = price;
        this.originalQty = qty;
        this.remaining = qty;
    }

    public String id() {
        return id;
    }

    public Side side() {
        return side;
    }

    public int price() {
        return price;
    }

    public int originalQty() {
        return originalQty;
    }

    public int remaining() {
        return remaining;
    }

    public void fill(int qty) {
        remaining -= qty;
    }
}
