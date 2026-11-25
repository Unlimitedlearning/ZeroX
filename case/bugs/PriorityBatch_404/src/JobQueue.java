import java.util.PriorityQueue;

public final class JobQueue {
    private final PriorityQueue<Job> heap = new PriorityQueue<>(
        (a, b) -> Integer.compare(a.priority(), b.priority()));

    public void add(Job job) {
        heap.add(job);
    }

    public Job poll() {
        return heap.poll();
    }

    public boolean isEmpty() {
        return heap.isEmpty();
    }

    public int size() {
        return heap.size();
    }
}
