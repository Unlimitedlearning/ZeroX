public final class Job {
    private final String id;
    private final int priority;
    private final int sequence;

    public Job(String id, int priority, int sequence) {
        this.id = id;
        this.priority = priority;
        this.sequence = sequence;
    }

    public String id() {
        return id;
    }

    public int priority() {
        return priority;
    }

    public int sequence() {
        return sequence;
    }
}
