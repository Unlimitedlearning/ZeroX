/** 入度表的只读快照。 */
public class IndegTable {
    private final int[] values;

    public IndegTable(int[] values) {
        this.values = values;
    }

    public int at(int i) {
        return values[i];
    }

    public int length() {
        return values.length;
    }
}
