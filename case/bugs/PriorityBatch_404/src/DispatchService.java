import java.util.Arrays;
import java.util.List;

public final class DispatchService {
    private final BatchRunner runner;

    public DispatchService(JobQueue queue) {
        this.runner = new BatchRunner(queue);
    }

    public List<String> dispatch(Job... jobs) {
        for (Job j : jobs) {
            runner.add(j);
        }
        return runner.drain();
    }

    public static List<String> ids(String... values) {
        return Arrays.asList(values);
    }
}
