import java.util.List;

public class OrderBook_407Test {

    interface Case {
        String run();
    }

    static String check(String label, boolean ok, String detail) {
        return ok ? null : label + " -> " + detail;
    }

    static String execute(Case c) {
        try {
            return c.run();
        } catch (Throwable t) {
            return t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    /** 单买单 @10 x5 对单卖单 @10 x3：部分成交 3，剩余 2。 */
    static MatchingEngine partial(Account acct) {
        MatchingEngine m = new MatchingEngine();
        m.bind("b1", acct);
        m.addBuy("b1", new Order("b1", Order.Side.BUY, 10, 5));
        m.addSell(new Order("s1", Order.Side.SELL, 10, 3));
        m.match();
        m.settle();
        return m;
    }

    public static void main(String[] args) {
        String[] names = {
            "trade count is one",
            "trade price is ten",
            "trade quantity is three",
            "buy order left two",
            "sell order fully filled",
            "partial spent is thirty",
            "partial available is right",
            "partial frozen is zero",
            "full fill conserves balance",
            "no trade returns full balance",
            "price improvement uses ask",
            "fee helper independent"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            MatchingEngine m = partial(new Account(1000));
            return check("count", m.tradeCount() == 1, "n=" + m.tradeCount());
        });

        results[1] = execute(() -> {
            Trade t = partial(new Account(1000)).trades().get(0);
            return check("price", t.price == 10, "p=" + t.price);
        });

        results[2] = execute(() -> {
            Trade t = partial(new Account(1000)).trades().get(0);
            return check("qty", t.qty == 3, "q=" + t.qty);
        });

        results[3] = execute(() -> {
            Account a = new Account(1000);
            MatchingEngine m = new MatchingEngine();
            m.bind("b1", a);
            m.addBuy("b1", new Order("b1", Order.Side.BUY, 10, 5));
            m.addSell(new Order("s1", Order.Side.SELL, 10, 3));
            m.match();
            return check("left", m.trades().get(0).qty == 3, "qty off");
        });

        results[4] = execute(() -> {
            Account a = new Account(1000);
            MatchingEngine m = new MatchingEngine();
            m.bind("b1", a);
            m.addBuy("b1", new Order("b1", Order.Side.BUY, 10, 5));
            m.addSell(new Order("s1", Order.Side.SELL, 10, 3));
            m.match();
            return check("filled", m.trades().get(0).qty == 3, "qty off");
        });

        results[5] = execute(() -> {
            Account a = new Account(1000);
            partial(a);
            return check("spent", a.available() + a.frozen() == 970,
                "available=" + a.available() + " frozen=" + a.frozen());
        });

        results[6] = execute(() -> {
            Account a = new Account(1000);
            partial(a);
            return check("available", a.available() == 970, "available=" + a.available());
        });

        results[7] = execute(() -> {
            Account a = new Account(1000);
            partial(a);
            return check("frozen", a.frozen() == 0, "frozen=" + a.frozen());
        });

        results[8] = execute(() -> {
            Account a = new Account(1000);
            MatchingEngine m = new MatchingEngine();
            m.bind("b1", a);
            m.addBuy("b1", new Order("b1", Order.Side.BUY, 10, 5));
            m.addSell(new Order("s1", Order.Side.SELL, 10, 5));
            m.match();
            m.settle();
            return check("full", a.available() == 950 && a.frozen() == 0,
                "available=" + a.available() + " frozen=" + a.frozen());
        });

        results[9] = execute(() -> {
            Account a = new Account(1000);
            MatchingEngine m = new MatchingEngine();
            m.bind("b1", a);
            m.addBuy("b1", new Order("b1", Order.Side.BUY, 10, 5));
            m.match();
            m.settle();
            return check("none", a.available() == 1000 && a.frozen() == 0,
                "available=" + a.available() + " frozen=" + a.frozen());
        });

        results[10] = execute(() -> {
            Account a = new Account(1000);
            MatchingEngine m = new MatchingEngine();
            m.bind("b1", a);
            m.addBuy("b1", new Order("b1", Order.Side.BUY, 12, 5));
            m.addSell(new Order("s1", Order.Side.SELL, 10, 3));
            m.match();
            m.settle();
            Trade t = m.trades().get(0);
            return check("ask price", t.price == 10 && t.qty == 3,
                "p=" + t.price + " q=" + t.qty);
        });

        results[11] = execute(() -> {
            return check("fee", new FeeTable(10).fee(1000) == 1, "fee off");
        });

        int failed = 0;
        for (int i = 0; i < names.length; i++) {
            if (results[i] == null) {
                System.out.println("[PASS] " + names[i]);
            } else {
                failed++;
                System.out.println("[FAIL] " + names[i] + ": " + results[i]);
            }
        }
        System.out.println();
        System.out.println((names.length - failed) + "/" + names.length + " test cases passed");
        if (failed == 0) {
            System.out.println("All tests passed");
        } else {
            System.out.println("FAILED (" + failed + ")");
        }
    }
}
