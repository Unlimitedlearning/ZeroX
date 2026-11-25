import java.util.List;

/** 调度器：按拓扑序返回任务名，附带去重后的用户可见序列。 */
public class TaskScheduler {
    private final TopoSort sorter = new TopoSort();

    public List<Integer> rawOrder(Graph g) {
        return sorter.order(g);
    }

    public int distinctCount(List<Integer> order) {
        java.util.Set<Integer> seen = new java.util.LinkedHashSet<>();
        seen.addAll(order);
        return seen.size();
    }
}
