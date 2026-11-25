import java.util.ArrayList;
import java.util.List;

public final class RecordPager {
    private final RecordStore store;

    public RecordPager(RecordStore store) {
        this.store = store;
    }

    public List<Record> after(long lastId, int size) {
        List<Record> all = store.records();
        int lo = 0;
        int hi = all.size();
        while (lo < hi) {
            int mid = (lo + hi) >>> 1;
            if (all.get(mid).id() <= lastId) {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        List<Record> out = new ArrayList<>();
        for (int i = lo; i < all.size() && out.size() < size; i++) {
            out.add(all.get(i));
        }
        return out;
    }
}
