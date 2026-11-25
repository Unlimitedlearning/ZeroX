import java.util.ArrayList;
import java.util.List;

public final class IngestService {
    private final DedupQueue queue = new DedupQueue();
    private int duplicates;

    public int ingest(List<Envelope> batch) {
        int added = 0;
        for (Envelope e : batch) {
            if (queue.offer(e)) {
                added++;
            } else {
                duplicates++;
            }
        }
        return added;
    }

    public int duplicates() {
        return duplicates;
    }

    public int seenCount() {
        return queue.seenCount();
    }

    public List<String> acceptedKinds() {
        List<String> out = new ArrayList<>();
        for (Envelope e : queue.exposed()) {
            out.add(e.kind());
        }
        return out;
    }
}
