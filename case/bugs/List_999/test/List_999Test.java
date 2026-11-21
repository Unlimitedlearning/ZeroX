import java.util.Arrays;

public class List_999Test {

    interface Case {
        String run() throws Exception;
    }

    static String difference(String label, String expected, String actual) {
        if (expected.equals(actual)) {
            return null;
        }
        return label + " expected " + expected + " but got " + actual;
    }

    static String firstDifference(String... differences) {
        for (String difference : differences) {
            if (difference != null) {
                return difference;
            }
        }
        return null;
    }

    static String text(int[] values) {
        return Arrays.toString(values);
    }

    static String execute(final Case testCase) {
        final Object[] outcome = new Object[1];
        Thread worker = new Thread(new Runnable() {
            public void run() {
                try {
                    outcome[0] = testCase.run();
                } catch (Throwable error) {
                    outcome[0] = error.getClass().getSimpleName() + ": " + error.getMessage();
                }
            }
        });
        worker.setDaemon(true);
        worker.start();
        try {
            worker.join(3000L);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
        if (worker.isAlive()) {
            worker.interrupt();
            return "no result after 3000ms";
        }
        return (String) outcome[0];
    }

    public static void main(String[] args) {
        String[] names = {
            "empty list",
            "single element",
            "unique lookup",
            "count duplicates",
            "last index of duplicates",
            "range over duplicates",
            "remove all duplicates",
            "insert keeps order",
            "nearest index",
            "missing key"
        };
        String[] results = new String[names.length];

        results[0] = execute(() -> {
            SortedInts list = new SortedInts(new int[0]);
            return firstDifference(
                    difference("size", "0", "" + list.size()),
                    difference("lowerBound(5)", "0", "" + list.lowerBound(5)),
                    difference("upperBound(5)", "0", "" + list.upperBound(5)),
                    difference("indexOfLast(5)", "-1", "" + list.indexOfLast(5)),
                    difference("count(5)", "0", "" + list.count(5)),
                    difference("nearestIndex(5)", "-1", "" + list.nearestIndex(5)),
                    difference("toArray", "[]", text(list.toArray())));
        });

        results[1] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{7});
            return firstDifference(
                    difference("size", "1", "" + list.size()),
                    difference("get(0)", "7", "" + list.get(0)),
                    difference("indexOfLast(7)", "0", "" + list.indexOfLast(7)),
                    difference("count(7)", "1", "" + list.count(7)),
                    difference("lowerBound(9)", "1", "" + list.lowerBound(9)),
                    difference("nearestIndex(9)", "0", "" + list.nearestIndex(9)));
        });

        results[2] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{9, 1, 7, 3, 5});
            return firstDifference(
                    difference("toArray", "[1, 3, 5, 7, 9]", text(list.toArray())),
                    difference("lowerBound(5)", "2", "" + list.lowerBound(5)),
                    difference("upperBound(5)", "3", "" + list.upperBound(5)),
                    difference("indexOfLast(5)", "2", "" + list.indexOfLast(5)),
                    difference("count(4)", "0", "" + list.count(4)),
                    difference("count(5)", "1", "" + list.count(5)),
                    difference("range(3, 7)", "[3, 5, 7]", text(list.range(3, 7))));
        });

        results[3] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{1, 2, 2, 2, 3, 4});
            return firstDifference(
                    difference("count(1)", "1", "" + list.count(1)),
                    difference("count(2)", "3", "" + list.count(2)),
                    difference("count(3)", "1", "" + list.count(3)),
                    difference("count(5)", "0", "" + list.count(5)));
        });

        results[4] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{1, 2, 2, 2, 3, 4});
            return firstDifference(
                    difference("lowerBound(2)", "1", "" + list.lowerBound(2)),
                    difference("upperBound(2)", "4", "" + list.upperBound(2)),
                    difference("indexOfLast(2)", "3", "" + list.indexOfLast(2)),
                    difference("indexOfLast(4)", "5", "" + list.indexOfLast(4)),
                    difference("indexOfLast(9)", "-1", "" + list.indexOfLast(9)));
        });

        results[5] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{1, 2, 2, 2, 3, 4});
            return firstDifference(
                    difference("range(2, 3)", "[2, 2, 2, 3]", text(list.range(2, 3))),
                    difference("range(4, 4)", "[4]", text(list.range(4, 4))),
                    difference("range(5, 6)", "[]", text(list.range(5, 6))));
        });

        results[6] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{1, 2, 2, 2, 3, 4});
            list.removeAll(2);
            return firstDifference(
                    difference("after removeAll(2)", "[1, 3, 4]", text(list.toArray())),
                    difference("size", "3", "" + list.size()),
                    difference("count(2)", "0", "" + list.count(2)),
                    difference("get(1)", "3", "" + list.get(1)));
        });

        results[7] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{1, 3, 5});
            list.insert(2);
            list.insert(3);
            list.insert(5);
            return firstDifference(
                    difference("after inserts", "[1, 2, 3, 3, 5, 5]", text(list.toArray())),
                    difference("size", "6", "" + list.size()),
                    difference("get(3)", "3", "" + list.get(3)));
        });

        results[8] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{1, 4, 9});
            return firstDifference(
                    difference("nearestIndex(0)", "0", "" + list.nearestIndex(0)),
                    difference("nearestIndex(2)", "0", "" + list.nearestIndex(2)),
                    difference("nearestIndex(3)", "1", "" + list.nearestIndex(3)),
                    difference("nearestIndex(6)", "1", "" + list.nearestIndex(6)),
                    difference("nearestIndex(13)", "2", "" + list.nearestIndex(13)));
        });

        results[9] = execute(() -> {
            SortedInts list = new SortedInts(new int[]{2, 4, 6});
            String missing = firstDifference(
                    difference("indexOfLast(9)", "-1", "" + list.indexOfLast(9)),
                    difference("count(9)", "0", "" + list.count(9)),
                    difference("lowerBound(9)", "3", "" + list.lowerBound(9)),
                    difference("range(9, 10)", "[]", text(list.range(9, 10))));
            if (missing != null) {
                return missing;
            }
            list.removeAll(9);
            return firstDifference(
                    difference("size after removeAll(9)", "3", "" + list.size()),
                    difference("toArray", "[2, 4, 6]", text(list.toArray())));
        });

        int failed = 0;
        for (int i = 0; i < names.length; i++) {
            if (results[i] == null) {
                System.out.println("[PASS] " + names[i]);
            } else {
                failed++;
                System.out.println("[FAIL] " + names[i] + ": " + results[i]);
            }
            System.out.flush();
        }

        System.out.println();
        System.out.println((names.length - failed) + "/" + names.length + " test cases passed");

        if (failed == 0) {
            System.out.println("All tests passed");
        } else {
            System.out.println("FAILED (" + failed + ")");
        }
        System.out.flush();
        System.exit(failed == 0 ? 0 : 1);
    }
}
