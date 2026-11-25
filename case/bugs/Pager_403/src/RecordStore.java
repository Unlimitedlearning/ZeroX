import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public final class RecordStore {
    private final List<Record> records = new ArrayList<>();

    public void insert(Record r) {
        records.add(r);
    }

    public List<Record> records() {
        return Collections.unmodifiableList(records);
    }

    public int size() {
        return records.size();
    }
}
