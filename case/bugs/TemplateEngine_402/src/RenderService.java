import java.util.HashSet;

public final class RenderService {
    private final TemplateRegistry registry;
    private final Resolver resolver;

    public RenderService(TemplateRegistry registry) {
        this.registry = registry;
        this.resolver = new Resolver(registry);
    }

    public String render(String name) {
        return resolver.render(name, new HashSet<>());
    }

    public int templateCount() {
        return registry.size();
    }
}
