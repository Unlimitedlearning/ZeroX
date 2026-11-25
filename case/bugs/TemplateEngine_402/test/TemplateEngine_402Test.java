import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

public class TemplateEngine_402Test {

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

    static RenderService diamond() {
        TemplateRegistry r = new TemplateRegistry();
        r.add(new Template("root", "R", "left", "right"));
        r.add(new Template("left", "L", "shared"));
        r.add(new Template("right", "R2", "shared"));
        r.add(new Template("shared", "S"));
        return new RenderService(r);
    }

    static RenderService chain() {
        TemplateRegistry r = new TemplateRegistry();
        r.add(new Template("a", "A", "b"));
        r.add(new Template("b", "B", "c"));
        r.add(new Template("c", "C"));
        return new RenderService(r);
    }

    public static void main(String[] args) {
        String[] names = {
            "leaf template renders",
            "linear chain renders",
            "shared child in two branches",
            "real cycle still detected",
            "self reference detected",
            "missing reference reported",
            "same template twice with fresh state",
            "deep chain renders",
            "child shared by three branches",
            "active set empty after render",
            "sibling branches do not contaminate"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            TemplateRegistry r = new TemplateRegistry();
            r.add(new Template("only", "X"));
            return check("leaf", "X".equals(new RenderService(r).render("only")),
                "got " + new RenderService(r).render("only"));
        });

        results[1] = execute(() -> {
            String out = chain().render("a");
            return check("chain", "ABC".equals(out), "got " + out);
        });

        results[2] = execute(() -> {
            String out = diamond().render("root");
            return check("diamond", "RLSR2S".equals(out), "got " + out);
        });

        results[3] = execute(() -> {
            TemplateRegistry r = new TemplateRegistry();
            r.add(new Template("a", "A", "b"));
            r.add(new Template("b", "B", "a"));
            String out = execute(() -> new RenderService(r).render("a"));
            return check("cycle", out.startsWith("IllegalStateException") && out.contains("cycle"),
                "got " + out);
        });

        results[4] = execute(() -> {
            TemplateRegistry r = new TemplateRegistry();
            r.add(new Template("self", "S", "self"));
            String out = execute(() -> new RenderService(r).render("self"));
            return check("self cycle", out.startsWith("IllegalStateException") && out.contains("cycle"),
                "got " + out);
        });

        results[5] = execute(() -> {
            TemplateRegistry r = new TemplateRegistry();
            r.add(new Template("a", "A", "ghost"));
            String out = execute(() -> new RenderService(r).render("a"));
            return check("missing", out.startsWith("IllegalArgumentException"), "got " + out);
        });

        results[6] = execute(() -> {
            RenderService s = chain();
            String a = s.render("a");
            String b = s.render("a");
            return check("twice", "ABC".equals(a) && "ABC".equals(b), "a=" + a + " b=" + b);
        });

        results[7] = execute(() -> {
            TemplateRegistry r = new TemplateRegistry();
            r.add(new Template("a", "A", "b"));
            r.add(new Template("b", "B", "c"));
            r.add(new Template("c", "C", "d"));
            r.add(new Template("d", "D"));
            String out = new RenderService(r).render("a");
            return check("deep", "ABCD".equals(out), "got " + out);
        });

        results[8] = execute(() -> {
            TemplateRegistry r = new TemplateRegistry();
            r.add(new Template("root", "0", "p", "q", "s"));
            r.add(new Template("p", "1", "leaf"));
            r.add(new Template("q", "2", "leaf"));
            r.add(new Template("s", "3", "leaf"));
            r.add(new Template("leaf", "L"));
            String out = new RenderService(r).render("root");
            return check("three branches", "01L2L3L".equals(out), "got " + out);
        });

        results[9] = execute(() -> {
            TemplateRegistry r = new TemplateRegistry();
            r.add(new Template("a", "A", "b"));
            r.add(new Template("b", "B"));
            Set<String> active = new HashSet<>();
            new Resolver(r).render("a", active);
            return check("active empty", active.isEmpty(), "active=" + active);
        });

        results[10] = execute(() -> {
            RenderService s = diamond();
            String first = s.render("root");
            String second = s.render("root");
            return check("siblings", "RLSR2S".equals(first) && "RLSR2S".equals(second),
                "first=" + first + " second=" + second);
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
