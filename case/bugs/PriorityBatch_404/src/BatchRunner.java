import java.util.ArrayList;
import java.util.List;

public final class BatchRunner {
    private final JobQueue queue;

    public BatchRunner(JobQueue queue) {
        this.queue = queue;
    }

    public void add(Job job) {
        queue.add(job);
    }

    public List<String> drain() {
        List<String> out = new ArrayList<>();
        while (!queue.isEmpty()) {
            out.add(queue.poll().id());
        }
        return out;
    }
}
