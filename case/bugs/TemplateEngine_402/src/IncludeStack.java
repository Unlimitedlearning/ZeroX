import java.util.ArrayList;
import java.util.List;

public final class IncludeStack {
    private final List<String> path = new ArrayList<>();
    private int includes;

    public void push(String name) {
        path.add(name);
        includes++;
    }

    public void pop() {
        if (!path.isEmpty()) {
            path.remove(path.size() - 1);
        }
    }

    public String current() {
        return path.isEmpty() ? "" : path.get(path.size() - 1);
    }

    public int includes() {
        return includes;
    }
}
