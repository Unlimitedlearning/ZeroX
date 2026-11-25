public final class Record {
    private final long id;
    private final String value;

    public Record(long id, String value) {
        this.id = id;
        this.value = value;
    }

    public long id() {
        return id;
    }

    public String value() {
        return value;
    }
}
