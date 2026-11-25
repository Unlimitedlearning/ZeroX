import java.util.Arrays;
import java.util.List;

public final class Template {
    private final String name;
    private final String body;
    private final List<String> refs;

    public Template(String name, String body, String... refs) {
        this.name = name;
        this.body = body;
        this.refs = Arrays.asList(refs);
    }

    public String name() {
        return name;
    }

    public String body() {
        return body;
    }

    public List<String> refs() {
        return refs;
    }
}
