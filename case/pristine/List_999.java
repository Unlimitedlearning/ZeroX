import java.util.Arrays;

public class List_999 {

    static final class SortedInts {
        private int[] data;
        private int size;

        SortedInts(int[] values) {
            int[] copy = values.clone();
            Arrays.sort(copy);
            this.size = copy.length;
            this.data = Arrays.copyOf(copy, Math.max(8, this.size * 2 + 1));
        }

        int size() {
            return size;
        }

        int get(int index) {
            if (index < 0 || index >= size) {
                throw new IndexOutOfBoundsException("index=" + index + " size=" + size);
            }
            return data[index];
        }

        int[] toArray() {
            return Arrays.copyOf(data, size);
        }

        int lowerBound(int key) {
            int lo = 0;
            int hi = size;
            while (lo < hi) {
                int mid = (lo + hi) >>> 1;
                if (data[mid] < key) {
                    lo = mid + 1;
                } else {
                    hi = mid;
                }
            }
            return lo;
        }

        int upperBound(int key) {
            int lo = 0;
            int hi = size;
            while (lo < hi) {
                int mid = (lo + hi) >>> 1;
                if (data[mid] <= key) {
                    lo = mid + 1;
                } else {
                    hi = mid;
                }
            }
            return lo;
        }

        int indexOfLast(int key) {
            int index = lowerBound(key);
            if (index < size && data[index] == key) {
                return index;
            }
            return -1;
        }

        int count(int key) {
            int first = lowerBound(key);
            int last = indexOfLast(key);
            if (last < 0) {
                return 0;
            }
            return last - first + 1;
        }

        int[] range(int from, int to) {
            int start = lowerBound(from);
            int end = upperBound(to);
            if (end <= start) {
                return new int[0];
            }
            return Arrays.copyOfRange(data, start, end);
        }

        void removeAll(int key) {
            int last = indexOfLast(key);
            if (last < 0) {
                return;
            }
            int first = lowerBound(key);
            int tail = last + 1;
            System.arraycopy(data, tail, data, first, size - tail);
            size -= tail - first;
        }

        void insert(int key) {
            if (size == data.length) {
                data = Arrays.copyOf(data, data.length * 2 + 1);
            }
            int position = upperBound(key);
            System.arraycopy(data, position, data, position + 1, size - position);
            data[position] = key;
            size++;
        }

        int nearestIndex(int key) {
            if (size == 0) {
                return -1;
            }
            int position = lowerBound(key);
            if (position == 0) {
                return 0;
            }
            if (position >= size) {
                return size - 1;
            }
            int leftGap = key - data[position - 1];
            int rightGap = data[position] - key;
            if (rightGap < leftGap) {
                return position;
            }
            return position - 1;
        }
    }

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
