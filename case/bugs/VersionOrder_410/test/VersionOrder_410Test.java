import java.util.Arrays;
import java.util.List;

public class VersionOrder_410Test {

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

    static int cmp(String a, String b) {
        return new Version(a).compareTo(new Version(b));
    }

    static String sorted(String... vs) {
        return String.join(",", new VersionRange(vs[0], vs[vs.length - 1])
            .sortStrings(Arrays.asList(vs)));
    }

    public static void main(String[] args) {
        String[] names = {
            "equal versions compare zero",
            "single digit ascending",
            "single digit descending",
            "mixed digits ascending",
            "mixed digits descending",
            "leading segment decides",
            "missing segment is zero",
            "trailing zero equals shorter",
            "range contains middle",
            "range excludes above",
            "range with mixed digits",
            "sort orders numerically",
            "tag helper independent",
            "segment parser independent"
        };
        String[] results = new String[names.length];

        results[0] = execute(() ->
            check("equal", cmp("1.2", "1.2") == 0, "got=" + cmp("1.2", "1.2")));

        results[1] = execute(() ->
            check("asc", cmp("1.8", "1.9") < 0, "got=" + cmp("1.8", "1.9")));

        results[2] = execute(() ->
            check("desc", cmp("1.9", "1.8") > 0, "got=" + cmp("1.9", "1.8")));

        results[3] = execute(() ->
            check("mixed asc", cmp("1.9", "1.10") < 0, "got=" + cmp("1.9", "1.10")));

        results[4] = execute(() ->
            check("mixed desc", cmp("1.10", "1.9") > 0, "got=" + cmp("1.10", "1.9")));

        results[5] = execute(() ->
            check("leading", cmp("2.0", "1.99") > 0, "got=" + cmp("2.0", "1.99")));

        results[6] = execute(() ->
            check("missing", cmp("1.0.0", "1") == 0, "got=" + cmp("1.0.0", "1")));

        results[7] = execute(() ->
            check("trailing", cmp("1.2.0", "1.2") == 0, "got=" + cmp("1.2.0", "1.2")));

        results[8] = execute(() -> {
            VersionRange r = new VersionRange("1.2", "2.0");
            return check("contains", r.contains("1.10"), "1.10 excluded");
        });

        results[9] = execute(() -> {
            VersionRange r = new VersionRange("1.2", "2.0");
            return check("excludes", !r.contains("2.1"), "2.1 included");
        });

        results[10] = execute(() -> {
            VersionRange r = new VersionRange("1.9", "1.11");
            return check("mixed range", r.contains("1.10"), "1.10 excluded");
        });

        results[11] = execute(() -> {
            List<String> xs = Arrays.asList("1.9", "1.10", "1.8");
            String got = String.join(",", new VersionRange("1.8", "1.10").sortStrings(xs));
            return check("sort", "1.8,1.9,1.10".equals(got), "got=" + got);
        });

        results[12] = execute(() ->
            check("tag", new SemverTag("").isStable() && !new SemverTag("rc").isStable(),
                "tag off"));

        results[13] = execute(() ->
            check("parser", SegmentParser.count("1.2.3") == 3, "parser off"));

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
