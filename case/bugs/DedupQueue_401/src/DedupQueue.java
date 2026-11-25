import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public final class DedupQueue {
    private final Set<Envelope> seen = new HashSet<>();
    private final List<Envelope> accepted = new ArrayList<>();

    public boolean offer(Envelope e) {
        if (!seen.add(e)) {
            return false;
        }
        accepted.add(e);
        return true;
    }

    public int seenCount() {
        return seen.size();
    }

    public List<Envelope> exposed() {
        return accepted;
    }
}
