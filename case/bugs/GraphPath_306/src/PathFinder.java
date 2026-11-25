import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Deque;
import java.util.List;
import java.util.PriorityQueue;

public final class PathFinder {

    public static final class Result {
        public final int distance;
        public final List<Integer> path;

        Result(int distance, List<Integer> path) {
            this.distance = distance;
            this.path = path;
        }
    }

    public Result shortest(Graph graph, int from, int to) {
        int n = graph.size();
        int[] dist = new int[n];
        int[] prev = new int[n];
        Arrays.fill(dist, Integer.MAX_VALUE);
        Arrays.fill(prev, -1);
        dist[from] = 0;

        PriorityQueue<int[]> queue = new PriorityQueue<>((a, b) -> Integer.compare(a[1], b[1]));
        queue.offer(new int[] {from, 0});
        boolean[] visited = new boolean[n];

        while (!queue.isEmpty()) {
            int[] current = queue.poll();
            int u = current[0];
            if (visited[u]) {
                continue;
            }
            visited[u] = true;
            for (int v : graph.neighbors(u)) {
                int candidate = dist[u] + graph.weight(u, v);
                if (candidate < dist[v]) {
                    dist[v] = candidate;
                    prev[v] = prev[u];
                    queue.offer(new int[] {v, candidate});
                }
            }
        }

        if (dist[to] == Integer.MAX_VALUE) {
            return new Result(-1, new ArrayList<>());
        }
        List<Integer> path = new ArrayList<>();
        boolean[] seen = new boolean[n];
        for (int at = to; at != -1 && !seen[at]; at = prev[at]) {
            seen[at] = true;
            path.add(0, at);
        }
        return new Result(dist[to], path);
    }
}
