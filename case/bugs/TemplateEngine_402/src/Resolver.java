import java.util.Set;

public final class Resolver {
    private final TemplateRegistry registry;

    public Resolver(TemplateRegistry registry) {
        this.registry = registry;
    }

    public String render(String name, Set<String> active) {
        if (!active.add(name)) {
            throw new IllegalStateException("cycle: " + name);
        }
        Template t = registry.get(name);
        if (t == null) {
            throw new IllegalArgumentException("missing: " + name);
        }
        StringBuilder sb = new StringBuilder(t.body());
        for (String ref : t.refs()) {
            sb.append(render(ref, active));
        }
        return sb.toString();
    }
}
