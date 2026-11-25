import java.util.ArrayList;
import java.util.List;

public final class Graph {
    private final List<List<Integer>> neighbors = new ArrayList<>();
    private final List<List<Integer>> weights = new ArrayList<>();

    public Graph(int nodes) {
        for (int i = 0; i < nodes; i++) {
            neighbors.add(new ArrayList<>());
            weights.add(new ArrayList<>());
        }
    }

    public int size() {
        return neighbors.size();
    }

    public void addEdge(int from, int to, int weight) {
        neighbors.get(from).add(to);
        weights.get(from).add(weight);
    }

    public List<Integer> neighbors(int node) {
        return neighbors.get(node);
    }

    public int weight(int from, int to) {
        List<Integer> targets = neighbors.get(from);
        for (int i = 0; i < targets.size(); i++) {
            if (targets.get(i) == to) {
                return weights.get(from).get(i);
            }
        }
        return -1;
    }
}
