import java.util.PriorityQueue;

/** Priority queue of (node, distance) pairs. */
public final class WeightQueue {
    private final PriorityQueue<int[]> queue =
            new PriorityQueue<>((a, b) -> Integer.compare(a[1], b[1]));

    public void offer(int node, int distance) {
        queue.offer(new int[] {node, distance});
    }

    public int[] poll() {
        return queue.poll();
    }

    public int size() {
        return queue.size();
    }
}
