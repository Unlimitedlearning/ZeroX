public final class CallService {
    private final Breaker breaker;

    public CallService(Breaker breaker) {
        this.breaker = breaker;
    }

    public String call(boolean ok) {
        if (!breaker.allow()) {
            return "rejected";
        }
        if (ok) {
            breaker.recordSuccess();
            return "ok";
        }
        breaker.recordFailure();
        return "failed";
    }
}
