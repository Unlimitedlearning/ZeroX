import java.io.PrintWriter;
import java.io.StringWriter;

public class RenderService_7Test {

    interface Case {
        String run();
    }

    static String check(String label, boolean ok, String detail) {
        return ok ? null : label + " -> " + detail;
    }

    static String first(String... diffs) {
        for (String d : diffs) {
            if (d != null) {
                return d;
            }
        }
        return null;
    }

    static String execute(Case c) {
        try {
            return c.run();
        } catch (Throwable t) {
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            return sw.toString().trim();
        }
    }

    public static void main(String[] args) {
        String[] names = {
            "capacity reported",
            "render first slot",
            "render second slot",
            "render middle slot",
            "render last slot",
            "describe first slot",
            "describe last slot",
            "render alternate prefix"
        };
        String[] results = new String[names.length];

        RenderService svc = new RenderService(4, "s");

        results[0] = execute(() ->
            check("capacity", svc.capacity() == 4, "expected 4, got " + svc.capacity()));

        results[1] = execute(() ->
            check("first label", "[s0]".equals(svc.render(0)), "expected [s0], got " + svc.render(0)));

        results[2] = execute(() ->
            check("second label", "[s1]".equals(svc.render(1)), "expected [s1], got " + svc.render(1)));

        results[3] = execute(() ->
            check("middle label", "[s2]".equals(svc.render(2)), "expected [s2], got " + svc.render(2)));

        results[4] = execute(() ->
            check("last label", "[s3]".equals(svc.render(3)), "expected [s3], got " + svc.render(3)));

        results[5] = execute(() ->
            check("first describe", "0:s0".equals(svc.describe(0)), "expected 0:s0, got " + svc.describe(0)));

        results[6] = execute(() ->
            check("last describe", "3:s3".equals(svc.describe(3)), "expected 3:s3, got " + svc.describe(3)));

        results[7] = execute(() -> {
            RenderService alt = new RenderService(3, "k");
            return check("alternate last", "[k2]".equals(alt.render(2)), "expected [k2], got " + alt.render(2));
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
