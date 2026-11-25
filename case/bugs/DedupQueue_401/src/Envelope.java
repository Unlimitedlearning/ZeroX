import java.util.Objects;

public final class Envelope {
    private final String kind;
    private final String payload;

    public Envelope(String kind, String payload) {
        this.kind = kind;
        this.payload = payload;
    }

    public String kind() {
        return kind;
    }

    public String payload() {
        return payload;
    }

    @Override
    public boolean equals(Object other) {
        if (!(other instanceof Envelope)) {
            return false;
        }
        Envelope e = (Envelope) other;
        return Objects.equals(kind, e.kind) && Objects.equals(payload, e.payload);
    }
}
