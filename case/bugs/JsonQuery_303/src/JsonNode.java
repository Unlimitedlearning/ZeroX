import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 极简 JSON 树节点：object / array / scalar。 */
public class JsonNode {
    public enum Kind { OBJECT, ARRAY, SCALAR }

    private final Kind kind;
    private final String scalar;
    private final Map<String, JsonNode> object;
    private final List<JsonNode> array;

    private JsonNode(Kind kind, String scalar) {
        this.kind = kind;
        this.scalar = scalar;
        this.object = new LinkedHashMap<>();
        this.array = new ArrayList<>();
    }

    public static JsonNode scalar(String s) {
        return new JsonNode(Kind.SCALAR, s);
    }

    public static JsonNode object() {
        return new JsonNode(Kind.OBJECT, null);
    }

    public static JsonNode array() {
        return new JsonNode(Kind.ARRAY, null);
    }

    public JsonNode put(String key, JsonNode child) {
        object.put(key, child);
        return this;
    }

    public JsonNode add(JsonNode child) {
        array.add(child);
        return this;
    }

    public Kind kind() {
        return kind;
    }

    public String scalar() {
        return scalar;
    }

    public JsonNode get(String key) {
        return object.get(key);
    }

    public JsonNode at(int i) {
        return array.get(i);
    }

    public int size() {
        return kind == Kind.ARRAY ? array.size() : object.size();
    }

    public boolean has(String key) {
        return object.containsKey(key);
    }
}
