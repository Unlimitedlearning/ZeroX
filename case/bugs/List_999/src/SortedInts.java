import java.util.Arrays;

public final class SortedInts {
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
