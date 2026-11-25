public final class Breaker {
    public static final int FAILURE_LIMIT = 3;
    public static final int SUCCESS_LIMIT = 3;

    private BreakerState state = BreakerState.CLOSED;
    private int failures;
    private int halfSuccesses;

    public BreakerState state() {
        return state;
    }

    public int failures() {
        return failures;
    }

    public int halfSuccesses() {
        return halfSuccesses;
    }

    public boolean allow() {
        return state != BreakerState.OPEN;
    }

    public void cooldown() {
        if (state == BreakerState.OPEN) {
            state = BreakerState.HALF_OPEN;
        }
    }

    public void recordSuccess() {
        if (state == BreakerState.HALF_OPEN) {
            halfSuccesses++;
            if (halfSuccesses >= SUCCESS_LIMIT) {
                state = BreakerState.CLOSED;
                failures = 0;
            }
        } else {
            failures = 0;
        }
    }

    public void recordFailure() {
        if (state == BreakerState.HALF_OPEN) {
            trip();
            return;
        }
        failures++;
        if (failures >= FAILURE_LIMIT) {
            trip();
        }
    }

    private void trip() {
        state = BreakerState.OPEN;
        failures = 0;
    }
}
