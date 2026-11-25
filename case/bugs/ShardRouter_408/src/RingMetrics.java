/** 路由统计快照：命中与未命中计数。 */
public class RingMetrics {
    private int routed;
    private int missed;

    public void routedOne() {
        routed++;
    }

    public void missedOne() {
        missed++;
    }

    public double hitRate() {
        int total = routed + missed;
        return total == 0 ? 0.0 : (double) routed / total;
    }
}
