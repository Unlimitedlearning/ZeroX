public final class QuotaRule {
    public static void check(int quantity, int limit) {
        if (quantity <= 0) {
            throw new IllegalArgumentException("invalid quantity");
        }
        if (quantity > limit) {
            throw new IllegalArgumentException("quota exceeded");
        }
    }
}
