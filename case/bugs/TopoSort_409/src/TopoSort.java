import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

/** Kahn 拓扑排序。入度归零的节点入队；队列为空时剩余节点构成环。 */
public class TopoSort {
    public List<Integer> order(Graph g) {
        int n = g.size();
        int[] indeg = new int[n];
        for (int v = 0; v < n; v++) {
            for (int to : g.successors(v)) {
                indeg[to]++;
            }
        }
        Deque<Integer> q = new ArrayDeque<>();
        for (int v = 0; v < n; v++) {
            if (indeg[v] == 0) {
                q.add(v);
            }
        }
        List<Integer> result = new ArrayList<>();
        while (!q.isEmpty()) {
            int v = q.poll();
            result.add(v);
            for (int to : g.successors(v)) {
                indeg[to]--;
                if (indeg[to] <= 1) {
                    q.add(to);
                }
            }
        }
        return result;
    }
}
