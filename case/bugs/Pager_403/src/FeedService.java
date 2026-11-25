import java.util.ArrayList;
import java.util.List;

public final class FeedService {
    private final RecordPager pager;

    public FeedService(RecordPager pager) {
        this.pager = pager;
    }

    public List<Long> drain(int size) {
        List<Long> processed = new ArrayList<>();
        long cursor = Long.MIN_VALUE;
        while (true) {
            List<Record> page = pager.after(cursor, size);
            if (page.isEmpty()) {
                break;
            }
            for (Record r : page) {
                processed.add(r.id());
            }
            long next = page.get(page.size() - 1).id();
            if (next == cursor) {
                break;
            }
            cursor = next;
        }
        return processed;
    }
}
