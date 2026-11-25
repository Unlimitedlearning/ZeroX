/** Coupon ledger: tracks how many coupons are still available. */
public final class CouponLedger {
    private int remaining;

    public CouponLedger(int remaining) {
        this.remaining = remaining;
    }

    public int reserve(int quantity) {
        remaining -= quantity;
        return remaining;
    }

    public int get() {
        return remaining;
    }
}
