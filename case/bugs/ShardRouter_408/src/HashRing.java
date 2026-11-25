import java.util.ArrayList;
import java.util.List;

/** 一致性哈希环：每个物理节点映射到多个虚拟点，按键顺时针找第一个虚拟点。 */
public class HashRing {
    static class Point {
        final int hash;
        final String node;

        Point(int hash, String node) {
            this.hash = hash;
            this.node = node;
        }
    }

    private final List<Point> ring = new ArrayList<>();

    public void addNode(String node, int replicas) {
        for (int i = 0; i < replicas; i++) {
            ring.add(new Point(hash(node + "#" + i), node));
        }
        ring.sort((a, b) -> Integer.compare(a.hash, b.hash));
    }

    /** 从环上移除一个物理节点的虚拟点。 */
    public void removeNode(String node) {
        for (int i = 0; i < ring.size(); i++) {
            if (ring.get(i).node.equals(node)) {
                ring.remove(i);
            }
        }
    }

    public String route(int key) {
        for (Point p : ring) {
            if (p.hash >= key) {
                return p.node;
            }
        }
        return ring.get(0).node;
    }

    public int pointCount() {
        return ring.size();
    }

    public boolean hasNode(String node) {
        for (Point p : ring) {
            if (p.node.equals(node)) {
                return true;
            }
        }
        return false;
    }

    public static int hash(String s) {
        int h = 0;
        for (int i = 0; i < s.length(); i++) {
            h = h * 31 + s.charAt(i);
        }
        return h & 0x7fffffff;
    }
}
