public final class ReservationService {
    private final Inventory inventory;
    private int accepted;

    public ReservationService(Inventory inventory) {
        this.inventory = inventory;
    }

    public int reserve(String id, int quantity, int limit) {
        int remaining = inventory.get(id) - quantity;
        inventory.set(id, remaining);
        QuotaRule.check(quantity, limit);
        if (remaining < 0) {
            throw new IllegalStateException("out of stock");
        }
        accepted++;
        return remaining;
    }

    public int accepted() {
        return accepted;
    }

    public int stockOf(String id) {
        return inventory.get(id);
    }
}
