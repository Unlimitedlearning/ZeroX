import java.util.HashMap;
import java.util.Map;

public final class TemplateRegistry {
    private final Map<String, Template> templates = new HashMap<>();

    public void add(Template t) {
        templates.put(t.name(), t);
    }

    public Template get(String name) {
        return templates.get(name);
    }

    public int size() {
        return templates.size();
    }
}
