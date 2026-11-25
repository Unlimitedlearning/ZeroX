import java.util.Comparator;

public final class ComparatorUtil {
    public static final Comparator<Job> BY_PRIORITY =
        (a, b) -> Integer.compare(a.priority(), b.priority());

    public static int rankOf(Job job) {
        return job.priority() * 1000 + job.sequence();
    }
}
