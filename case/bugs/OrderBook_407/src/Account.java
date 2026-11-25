/** 账户余额：available 可用、frozen 冻结，成交从冻结扣。 */
public class Account {
    private int available;
    private int frozen;

    public Account(int balance) {
        this.available = balance;
    }

    public int available() {
        return available;
    }

    public int frozen() {
        return frozen;
    }

    public void freeze(int qty) {
        available -= qty;
        frozen += qty;
    }

    public void debitFrozen(int qty) {
        frozen -= qty;
    }

    public void release(int qty) {
        frozen -= qty;
        available += qty;
    }
}
