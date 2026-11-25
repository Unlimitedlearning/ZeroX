/** 查询统计：记录查询次数与命中数。 */
public class QueryStats {
    private int queries;
    private int hits;

    public void record(int found) {
        queries++;
        hits += found;
    }

    public int queries() {
        return queries;
    }

    public int hits() {
        return hits;
    }
}
