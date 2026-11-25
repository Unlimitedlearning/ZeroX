import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class Pager_403Test {

    interface Case {
        String run();
    }

    static String check(String label, boolean ok, String detail) {
        return ok ? null : label + " -> " + detail;
    }

    static String execute(Case c) {
        try {
            return c.run();
        } catch (Throwable t) {
            return t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    static void insert(RecordStore s, long... ids) {
        for (long id : ids) {
            s.insert(new Record(id, "v" + id));
        }
    }

    static List<Long> ids(List<Record> rows) {
        List<Long> out = new ArrayList<>();
        for (Record r : rows) {
            out.add(r.id());
        }
        return out;
    }

    static RecordStore store(long... values) {
        RecordStore s = new RecordStore();
        insert(s, values);
        return s;
    }

    public static void main(String[] args) {
        String[] names = {
            "empty store drains nothing",
            "ordered insert keeps order",
            "first page from ordered store",
            "out of order insert keeps order",
            "first page from shuffled store",
            "drain visits every record",
            "drain has no duplicate ids",
            "last page may be short",
            "cursor skips consumed ids",
            "late small id inserted in order",
            "later large id reached by drain"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            List<Long> out = new FeedService(new RecordPager(new RecordStore())).drain(2);
            return check("empty", out.isEmpty(), "got " + out);
        });

        results[1] = execute(() -> {
            List<Long> out = ids(store(1, 2, 3).records());
            return check("ordered", out.equals(Arrays.asList(1L, 2L, 3L)), "got " + out);
        });

        results[2] = execute(() -> {
            List<Long> out = ids(new RecordPager(store(1, 2, 3, 4, 5)).after(0, 2));
            return check("first page", out.equals(Arrays.asList(1L, 2L)), "got " + out);
        });

        results[3] = execute(() -> {
            List<Long> out = ids(store(5, 1, 3, 2, 4).records());
            return check("shuffled order", out.equals(Arrays.asList(1L, 2L, 3L, 4L, 5L)), "got " + out);
        });

        results[4] = execute(() -> {
            List<Long> out = ids(new RecordPager(store(5, 1, 3, 2, 4)).after(0, 2));
            return check("shuffled page", out.equals(Arrays.asList(1L, 2L)), "got " + out);
        });

        results[5] = execute(() -> {
            List<Long> out = new FeedService(new RecordPager(store(5, 1, 3, 2, 4))).drain(2);
            List<Long> want = Arrays.asList(1L, 2L, 3L, 4L, 5L);
            return check("complete", out.equals(want), "got " + out);
        });

        results[6] = execute(() -> {
            List<Long> out = new FeedService(new RecordPager(store(5, 1, 3, 2, 4))).drain(2);
            long distinct = out.stream().distinct().count();
            return check("no dup", distinct == out.size(), "got " + out);
        });

        results[7] = execute(() -> {
            List<Long> out = ids(new RecordPager(store(1, 2, 3)).after(1, 10));
            return check("short page", out.equals(Arrays.asList(2L, 3L)), "got " + out);
        });

        results[8] = execute(() -> {
            List<Long> out = ids(new RecordPager(store(1, 2, 3, 4)).after(3, 2));
            return check("cursor", out.equals(Arrays.asList(4L)), "got " + out);
        });

        results[9] = execute(() -> {
            RecordStore s = store(5, 8);
            s.insert(new Record(2, "v2"));
            List<Long> out = ids(s.records());
            return check("late small id", out.equals(Arrays.asList(2L, 5L, 8L)), "got " + out);
        });

        results[10] = execute(() -> {
            RecordStore s = store(5, 1, 3);
            FeedService feed = new FeedService(new RecordPager(s));
            List<Long> first = feed.drain(2);
            s.insert(new Record(9, "v9"));
            List<Long> out = new FeedService(new RecordPager(s)).drain(2);
            return check("large id later", first.equals(Arrays.asList(1L, 3L, 5L))
                    && out.equals(Arrays.asList(1L, 3L, 5L, 9L)),
                "first=" + first + " out=" + out);
        });

        int failed = 0;
        for (int i = 0; i < names.length; i++) {
            if (results[i] == null) {
                System.out.println("[PASS] " + names[i]);
            } else {
                failed++;
                System.out.println("[FAIL] " + names[i] + ": " + results[i]);
            }
        }
        System.out.println();
        System.out.println((names.length - failed) + "/" + names.length + " test cases passed");
        if (failed == 0) {
            System.out.println("All tests passed");
        } else {
            System.out.println("FAILED (" + failed + ")");
        }
    }
}
