import java.util.List;

public class JsonQuery_303Test {

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

    static JsonNode sample() {
        return JsonNode.object()
            .put("a", JsonNode.object().put("b", JsonNode.scalar("x")).put("c", JsonNode.scalar("y")))
            .put("list", JsonNode.array()
                .add(JsonNode.scalar("p")).add(JsonNode.scalar("q")).add(JsonNode.scalar("r")));
    }

    static String join(List<String> xs) {
        return String.join(",", xs);
    }

    public static void main(String[] args) {
        String[] names = {
            "single nested path",
            "single sibling path",
            "single array expansion",
            "missing path is empty",
            "batch of two paths",
            "batch of three paths",
            "batch mixing array expansion",
            "reused resolver second query",
            "reused resolver third query",
            "batch preserves order",
            "token classifies wildcard",
            "stats accumulate"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            List<String> r = new PathResolver().query(sample(), "a.b");
            return check("a.b", "x".equals(join(r)), "got=" + join(r));
        });

        results[1] = execute(() -> {
            List<String> r = new PathResolver().query(sample(), "a.c");
            return check("a.c", "y".equals(join(r)), "got=" + join(r));
        });

        results[2] = execute(() -> {
            List<String> r = new PathResolver().query(sample(), "list.[*]");
            return check("list", "p,q,r".equals(join(r)), "got=" + join(r));
        });

        results[3] = execute(() -> {
            List<String> r = new PathResolver().query(sample(), "a.missing");
            return check("missing", r.isEmpty(), "got=" + join(r));
        });

        results[4] = execute(() -> {
            List<String> r = new PathResolver().queryBatch(sample(), new String[]{"a.b", "a.c"});
            return check("two", "x,y".equals(join(r)), "got=" + join(r));
        });

        results[5] = execute(() -> {
            List<String> r = new PathResolver().queryBatch(sample(), new String[]{"a.b", "a.c", "a.b"});
            return check("three", "x,y,x".equals(join(r)), "got=" + join(r));
        });

        results[6] = execute(() -> {
            List<String> r = new PathResolver().queryBatch(sample(), new String[]{"list.[*]", "a.b"});
            return check("mixed", "p,q,r,x".equals(join(r)), "got=" + join(r));
        });

        results[7] = execute(() -> {
            PathResolver p = new PathResolver();
            JsonNode root = sample();
            p.query(root, "a.b");
            List<String> r = p.query(root, "a.c");
            return check("second", "y".equals(join(r)), "got=" + join(r));
        });

        results[8] = execute(() -> {
            PathResolver p = new PathResolver();
            JsonNode root = sample();
            p.query(root, "a.b");
            p.query(root, "a.c");
            List<String> r = p.query(root, "a.b");
            return check("third", "x".equals(join(r)), "got=" + join(r));
        });

        results[9] = execute(() -> {
            List<String> r = new PathResolver().queryBatch(sample(), new String[]{"a.c", "a.b"});
            return check("order", "y,x".equals(join(r)), "got=" + join(r));
        });

        results[10] = execute(() -> {
            return check("token", new PathToken("[*]").isWildcard()
                    && !new PathToken("a").isWildcard(), "classification off");
        });

        results[11] = execute(() -> {
            QueryStats s = new QueryStats();
            s.record(2);
            s.record(1);
            return check("stats", s.queries() == 2 && s.hits() == 3,
                "q=" + s.queries() + " h=" + s.hits());
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
