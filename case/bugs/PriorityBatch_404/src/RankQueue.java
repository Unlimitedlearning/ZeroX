import java.util.PriorityQueue;

public final class RankQueue {
    private final PriorityQueue<Job> heap = new PriorityQueue<>((a, b) -> {
        int c = Integer.compare(a.priority(), b.priority());
        return c != 0 ? c : Integer.compare(a.sequence(), b.sequence());
    });

    public void add(Job job) {
        heap.add(job);
    }

    public String next() {
        Job j = heap.poll();
        return j == null ? "" : j.id();
    }
}
