import java.util.ArrayList;
import java.util.List;

/** 有向图：节点用整数索引，边存于邻接表。 */
public class Graph {
    private final int n;
    private final List<List<Integer>> out;

    public Graph(int n) {
        this.n = n;
        this.out = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            out.add(new ArrayList<>());
        }
    }

    public void edge(int from, int to) {
        out.get(from).add(to);
    }

    public List<Integer> successors(int v) {
        return out.get(v);
    }

    public int size() {
        return n;
    }
}
