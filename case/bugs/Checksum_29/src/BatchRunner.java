import java.util.List;

public final class BatchRunner {
    private final TaskMask mask = new TaskMask();
    private int processed;
    private int failures;

    /** Returns how many items completed in this run. */
    public int run(List<WorkItem> items) {
        int before = processed;
        for (WorkItem item : items) {
            if (mask.has(item.bit())) {
                continue;
            }
            if (item.shouldFail()) {
                mask.clear(item.bit());
                failures++;
            } else {
                mask.mark(item.bit());
                processed++;
            }
        }
        return processed - before;
    }

    public boolean done(int bit) {
        return mask.has(bit);
    }

    public int mask() {
        return mask.value();
    }

    public int processed() {
        return processed;
    }

    public int failures() {
        return failures;
    }
}
