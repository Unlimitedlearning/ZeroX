import java.util.HashSet;
import java.util.Set;

public final class RetryLedger {
    private final Set<String> seen = new HashSet<>();
    private int attempts;

    public boolean record(String key) {
        attempts++;
        return seen.add(key);
    }

    public int seenCount() {
        return seen.size();
    }

    public int attempts() {
        return attempts;
    }
}
