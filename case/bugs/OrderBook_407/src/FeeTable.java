/** 费率表：按基点换算手续费。 */
public class FeeTable {
    private final int bps;

    public FeeTable(int bps) {
        this.bps = bps;
    }

    public int fee(int notional) {
        return notional * bps / 10000;
    }
}
