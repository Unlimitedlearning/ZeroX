import java.util.LinkedHashMap;
import java.util.Map;

/** 分片路由：包装哈希环，记录每个节点的副本数。 */
public class ShardRouter {
    private final HashRing ring = new HashRing();
    private final Map<String, Integer> replicas = new LinkedHashMap<>();

    public ShardRouter() {
        add("alpha", 3);
        add("beta", 3);
        add("gamma", 3);
    }

    public void add(String node, int n) {
        replicas.put(node, n);
        ring.addNode(node, n);
    }

    public void remove(String node) {
        replicas.remove(node);
        ring.removeNode(node);
    }

    public String route(int key) {
        return ring.route(key);
    }

    public int points() {
        return ring.pointCount();
    }

    public boolean has(String node) {
        return ring.hasNode(node);
    }

    public int declaredReplicas(String node) {
        return replicas.getOrDefault(node, 0);
    }
}
