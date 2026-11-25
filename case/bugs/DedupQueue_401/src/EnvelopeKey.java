import java.util.Objects;

public final class EnvelopeKey {
    private final String kind;
    private final long stamp;

    public EnvelopeKey(String kind, long stamp) {
        this.kind = kind;
        this.stamp = stamp;
    }

    @Override
    public boolean equals(Object other) {
        if (!(other instanceof EnvelopeKey)) {
            return false;
        }
        EnvelopeKey k = (EnvelopeKey) other;
        return Objects.equals(kind, k.kind) && stamp == k.stamp;
    }

    @Override
    public int hashCode() {
        return Objects.hash(kind, stamp);
    }
}
