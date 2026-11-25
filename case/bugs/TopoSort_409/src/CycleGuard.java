/** 有向图环检测工具。 */
public class CycleGuard {
    public static boolean wouldCycle(int from, int to) {
        return from == to;
    }

    public static int limit(int n) {
        return n < 0 ? 0 : n;
    }
}
