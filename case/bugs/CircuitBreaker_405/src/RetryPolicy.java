public final class RetryPolicy {
    private int attempts;
    private long backoffMs;

    public boolean shouldRetry(int failures) {
        attempts++;
        return failures < 3;
    }

    public void backoff(long ms) {
        backoffMs += ms;
    }

    public int attempts() {
        return attempts;
    }

    public long backoffMs() {
        return backoffMs;
    }
}
