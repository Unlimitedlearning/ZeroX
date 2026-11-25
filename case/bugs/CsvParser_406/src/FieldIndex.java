/** 字段索引助手：按列名取列号。 */
public class FieldIndex {
    private final java.util.List<String> fields;

    public FieldIndex(java.util.List<String> fields) {
        this.fields = fields;
    }

    public String at(int i) {
        return i < fields.size() ? fields.get(i) : "";
    }

    public int width() {
        return fields.size();
    }
}
