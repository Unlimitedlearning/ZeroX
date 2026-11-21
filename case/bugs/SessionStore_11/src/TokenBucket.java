public class TokenBucket {
    private double tokens;
    private long lastRefill;
    private double refillRate;
    private long maxIdleMs;

    public TokenBucket(double initialTokens, double refillRatePerSecond) {
        this.tokens = initialTokens;
        this.refillRate = refillRatePerSecond;
        this.lastRefill = System.currentTimeMillis();
        this.maxIdleMs = 1000L;
    }

    public double getTokens() {
        return tokens;
    }

    public long getLastRefill() {
        return lastRefill;
    }

    public void setLastRefill(long lastRefill) {
        this.lastRefill = lastRefill;
    }

    public void setMaxIdleMs(long maxIdleMs) {
        this.maxIdleMs = maxIdleMs;
    }

    public double getRefillRate() {
        return refillRate;
    }

    public long getMaxIdleMs() {
        return maxIdleMs;
    }

    public void setTokens(double tokens) {
        this.tokens = tokens;
    }

    public void setRefillRate(double refillRate) {
        this.refillRate = refillRate;
    }

    public double getCapacity() {
        return tokens + (refillRate / 1000.0 * maxIdleMs);
    }

    public void touch(long now) {
        this.lastRefill = now;
        this.tokens = Math.min(this.getCapacity(), this.tokens);
    }

    public boolean isStale(long now) {
        return now - lastRefill > maxIdleMs;
    }

    public void refill(long now) {
        long elapsed = now - lastRefill;
        double addedTokens = (elapsed / 1000.0) * refillRate;
        this.tokens = Math.min(this.getCapacity(), this.tokens + addedTokens);
        this.lastRefill = now;
    }

    public int size() {
        return (int) Math.round(tokens);
    }

    public boolean tryConsume(int count) {
        if (tokens >= count) {
            tokens -= count;
            return true;
        }
        return false;
    }
}
