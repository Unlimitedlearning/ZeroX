import java.util.Arrays;
import java.util.List;

public class DedupQueue_401Test {

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

    static Envelope env(String kind, String payload) {
        return new Envelope(kind, payload);
    }

    public static void main(String[] args) {
        String[] names = {
            "single message accepted",
            "same content new instance is duplicate",
            "different kind same payload both accepted",
            "same kind different payload both accepted",
            "seen count equals distinct messages",
            "first occurrence order retained",
            "duplicate across batches rejected",
            "mixed batch counts duplicate once",
            "empty payload deduplicated",
            "kind comparison is case sensitive",
            "key control group deduplicates",
            "kind difference survives hash collision",
            "payload difference survives hash collision"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            IngestService s = new IngestService();
            int n = s.ingest(Arrays.asList(env("note", "a")));
            return check("single", n == 1 && s.duplicates() == 0, "n=" + n + " dup=" + s.duplicates());
        });

        results[1] = execute(() -> {
            IngestService s = new IngestService();
            s.ingest(Arrays.asList(env("note", "a")));
            int n = s.ingest(Arrays.asList(env("note", "a")));
            return check("same instance content", n == 0 && s.duplicates() == 1,
                "n=" + n + " dup=" + s.duplicates());
        });

        results[2] = execute(() -> {
            IngestService s = new IngestService();
            int n = s.ingest(Arrays.asList(env("note", "a"), env("memo", "a")));
            return check("different kind", n == 2, "expected 2, got " + n);
        });

        results[3] = execute(() -> {
            IngestService s = new IngestService();
            int n = s.ingest(Arrays.asList(env("note", "a"), env("note", "b")));
            return check("different payload", n == 2, "expected 2, got " + n);
        });

        results[4] = execute(() -> {
            IngestService s = new IngestService();
            s.ingest(Arrays.asList(env("note", "a"), env("note", "a"), env("memo", "b")));
            return check("seen count", s.seenCount() == 2, "expected 2, got " + s.seenCount());
        });

        results[5] = execute(() -> {
            IngestService s = new IngestService();
            s.ingest(Arrays.asList(env("memo", "b"), env("note", "a")));
            List<String> kinds = s.acceptedKinds();
            return check("order", kinds.equals(Arrays.asList("memo", "note")), "got " + kinds);
        });

        results[6] = execute(() -> {
            IngestService s = new IngestService();
            s.ingest(Arrays.asList(env("note", "a")));
            s.ingest(Arrays.asList(env("memo", "b")));
            int n = s.ingest(Arrays.asList(env("note", "a")));
            return check("cross batch", n == 0 && s.duplicates() == 1,
                "n=" + n + " dup=" + s.duplicates());
        });

        results[7] = execute(() -> {
            IngestService s = new IngestService();
            int n = s.ingest(Arrays.asList(
                env("note", "a"), env("memo", "b"), env("note", "a"), env("note", "c")));
            return check("mixed batch", n == 3 && s.duplicates() == 1,
                "n=" + n + " dup=" + s.duplicates());
        });

        results[8] = execute(() -> {
            IngestService s = new IngestService();
            int n = s.ingest(Arrays.asList(env("note", ""), env("note", "")));
            return check("empty payload", n == 1, "expected 1, got " + n);
        });

        results[9] = execute(() -> {
            IngestService s = new IngestService();
            int n = s.ingest(Arrays.asList(env("Note", "a"), env("note", "a")));
            return check("case sensitive", n == 2, "expected 2, got " + n);
        });

        results[10] = execute(() -> {
            java.util.Set<EnvelopeKey> keys = new java.util.HashSet<>();
            boolean a = keys.add(new EnvelopeKey("note", 7L));
            boolean b = keys.add(new EnvelopeKey("note", 7L));
            return check("key control", a && !b && keys.size() == 1,
                "a=" + a + " b=" + b + " size=" + keys.size());
        });

        results[11] = execute(() -> {
            IngestService s = new IngestService();
            int n = s.ingest(Arrays.asList(env("Aa", "same"), env("BB", "same")));
            int h1 = env("Aa", "same").hashCode();
            int h2 = env("BB", "same").hashCode();
            return check("hash collision", h1 == h2 && n == 2 && s.seenCount() == 2,
                "h1=" + h1 + " h2=" + h2 + " n=" + n + " seen=" + s.seenCount());
        });

        results[12] = execute(() -> {
            IngestService s = new IngestService();
            int n = s.ingest(Arrays.asList(env("same", "Aa"), env("same", "BB")));
            int h1 = env("same", "Aa").hashCode();
            int h2 = env("same", "BB").hashCode();
            return check("payload collision", h1 == h2 && n == 2 && s.seenCount() == 2,
                "h1=" + h1 + " h2=" + h2 + " n=" + n + " seen=" + s.seenCount());
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
