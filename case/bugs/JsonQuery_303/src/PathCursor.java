/** 路径下降游标，持有当前节点、路径片段与读取位置。 */
public class PathCursor {
    private JsonNode node;
    private String[] parts;
    private int pos;

    public void reset(JsonNode root, String path) {
        this.node = root;
        this.parts = path.split("\\.");
    }

    /** 把位置归零。 */
    public void start() {
        this.pos = 0;
    }

    public boolean done() {
        return pos >= parts.length;
    }

    public String peek() {
        return parts[pos];
    }

    public void stepObject() {
        node = node.get(peek());
        pos++;
    }

    public void stepIndex(int k) {
        node = node.at(k);
        pos++;
    }

    public JsonNode current() {
        return node;
    }

    public int position() {
        return pos;
    }

    public void restore(JsonNode n, int p) {
        this.node = n;
        this.pos = p;
    }
}
