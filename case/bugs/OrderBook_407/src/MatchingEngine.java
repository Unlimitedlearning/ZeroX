import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** 简单撮合：买高价优先、卖低价优先；冻结与结算由账户处理。 */
public class MatchingEngine {
    private final List<Order> buys = new ArrayList<>();
    private final List<Order> sells = new ArrayList<>();
    private final List<Trade> trades = new ArrayList<>();
    private final Map<String, Account> accounts = new HashMap<>();

    public void bind(String accountId, Account account) {
        accounts.put(accountId, account);
    }

    public void addBuy(String accountId, Order o) {
        accounts.get(accountId).freeze(o.price() * o.originalQty());
        buys.add(o);
    }

    public void addSell(Order o) {
        sells.add(o);
    }

    public List<Trade> match() {
        buys.sort((a, b) -> Integer.compare(b.price(), a.price()));
        sells.sort((a, b) -> Integer.compare(a.price(), b.price()));
        for (Order b : buys) {
            for (Order s : sells) {
                if (b.remaining() == 0 || s.remaining() == 0) {
                    continue;
                }
                if (b.price() >= s.price()) {
                    int qty = Math.min(b.remaining(), s.remaining());
                    b.fill(qty);
                    s.fill(qty);
                    trades.add(new Trade(b.id(), s.id(), s.price(), qty));
                }
            }
        }
        return trades;
    }

    /** 结算：处理成交扣减与未成交部分的冻结。 */
    public void settle() {
        for (Trade t : trades) {
            Account acct = accounts.get(t.buyId);
            acct.debitFrozen(t.price * t.qty);
        }
        for (Order b : buys) {
            if (b.remaining() > 0) {
                Account acct = accounts.get(b.id());
                acct.release(b.price() * b.originalQty());
            }
        }
    }

    public List<Trade> trades() {
        return trades;
    }

    public int tradeCount() {
        return trades.size();
    }
}
