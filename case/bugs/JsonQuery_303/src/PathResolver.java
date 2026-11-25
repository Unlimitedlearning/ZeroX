import java.util.ArrayList;
import java.util.List;

/** 点分路径求值：支持对象属性与 [*] 数组展开。 */
public class PathResolver {
    private final PathCursor cursor = new PathCursor();

    public List<String> query(JsonNode root, String path) {
        return walk(root, path);
    }

    /** 一次求值多条路径，返回各路径命中标量的拼接列表。 */
    public List<String> queryBatch(JsonNode root, String[] paths) {
        List<String> out = new ArrayList<>();
        for (String p : paths) {
            out.addAll(walk(root, p));
        }
        return out;
    }

    private List<String> walk(JsonNode root, String path) {
        List<String> out = new ArrayList<>();
        cursor.reset(root, path);
        descend(out);
        return out;
    }

    private void descend(List<String> out) {
        if (cursor.done()) {
            JsonNode node = cursor.current();
            if (node.kind() == JsonNode.Kind.SCALAR) {
                out.add(node.scalar());
            }
            return;
        }
        String part = cursor.peek();
        if ("[*]".equals(part)) {
            JsonNode arr = cursor.current();
            int n = arr.size();
            for (int k = 0; k < n; k++) {
                int savedPos = cursor.position();
                cursor.stepIndex(k);
                descend(out);
                cursor.restore(arr, savedPos + 1);
            }
        } else {
            JsonNode next = cursor.current().get(part);
            if (next == null) {
                return;
            }
            cursor.stepObject();
            descend(out);
        }
    }
}
