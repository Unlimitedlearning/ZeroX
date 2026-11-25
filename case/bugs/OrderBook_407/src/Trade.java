public class Trade {
    public final String buyId;
    public final String sellId;
    public final int price;
    public final int qty;

    public Trade(String buyId, String sellId, int price, int qty) {
        this.buyId = buyId;
        this.sellId = sellId;
        this.price = price;
        this.qty = qty;
    }

    @Override
    public String toString() {
        return buyId + "/" + sellId + "@" + price + "x" + qty;
    }
}
