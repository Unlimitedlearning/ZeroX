import java.util.Arrays;

public class GraphPath_306Test {

    interface Case {
        String run();
    }

    static RouteService sample() {
        RouteService svc = new RouteService(5);
        svc.link(0, 1, 5);
        svc.link(0, 2, 1);
        svc.link(2, 1, 1);
        svc.link(1, 3, 1);
        svc.link(2, 3, 10);
        return svc;
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

    public static void main(String[] args) {
        String[] names = {
            "direct edge distance",
            "multi-hop distance",
            "path starts at source",
            "path ends at target",
            "path node count",
            "full path sequence",
            "each consecutive pair is an edge",
            "unreachable target",
            "path to direct neighbor",
            "path to two-hop neighbor"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            PathFinder.Result r = sample().route(0, 2);
            return check("distance", r.distance == 1, "expected 1, got " + r.distance);
        });

        results[1] = execute(() -> {
            PathFinder.Result r = sample().route(0, 3);
            return check("distance", r.distance == 3, "expected 3, got " + r.distance);
        });

        results[2] = execute(() -> {
            PathFinder.Result r = sample().route(0, 3);
            return check("source", !r.path.isEmpty() && r.path.get(0) == 0, "path=" + r.path);
        });

        results[3] = execute(() -> {
            PathFinder.Result r = sample().route(0, 3);
            return check("target", !r.path.isEmpty() && r.path.get(r.path.size() - 1) == 3,
                "path=" + r.path);
        });

        results[4] = execute(() -> {
            PathFinder.Result r = sample().route(0, 3);
            return check("size", r.path.size() == 4, "expected [0,2,1,3] (4 nodes), got " + r.path);
        });

        results[5] = execute(() -> {
            PathFinder.Result r = sample().route(0, 3);
            return check("sequence", r.path.equals(Arrays.asList(0, 2, 1, 3)),
                "expected [0, 2, 1, 3], got " + r.path);
        });

        results[6] = execute(() -> {
            RouteService svc = sample();
            PathFinder.Result r = svc.route(0, 3);
            if (r.path.size() < 2) {
                return check("edges", false, "path has no edge: " + r.path);
            }
            for (int i = 0; i + 1 < r.path.size(); i++) {
                if (svc.edgeWeight(r.path.get(i), r.path.get(i + 1)) < 0) {
                    return check("edges", false, "no edge " + r.path.get(i) + "->" + r.path.get(i + 1));
                }
            }
            return null;
        });

        results[7] = execute(() -> {
            PathFinder.Result r = sample().route(0, 4);
            return check("unreachable", r.distance == -1 && r.path.isEmpty(),
                "distance=" + r.distance + " path=" + r.path);
        });

        results[8] = execute(() -> {
            PathFinder.Result r = sample().route(0, 2);
            return check("direct path", r.path.equals(Arrays.asList(0, 2)),
                "expected [0, 2], got " + r.path);
        });

        results[9] = execute(() -> {
            PathFinder.Result r = sample().route(0, 1);
            return check("two-hop path", r.path.equals(Arrays.asList(0, 2, 1)),
                "expected [0, 2, 1], got " + r.path);
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
