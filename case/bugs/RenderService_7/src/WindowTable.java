import java.util.Arrays;

public class WindowTable {
    private int[] timestamps;
    private int windowSize;
    private int currentWindowStart;

    public WindowTable(int size) {
        this.windowSize = size;
        this.timestamps = new int[size];
        this.currentWindowStart = 0;
    }

    public int get(int index) {
        if (index < 0 || index >= timestamps.length) {
            return -1;
        }
        return timestamps[index];
    }

    public void resolve(int index, int timestamp) {
        if (index < 0 || index >= timestamps.length) {
            throw new IndexOutOfBoundsException("Index out of bounds");
        }
        timestamps[index] = timestamp;
    }

    public int size() {
        return timestamps.length;
    }

    public String label() {
        return "WindowData";
    }

    public long getWindowStart() {
        return currentWindowStart;
    }

    public void advanceWindow() {
        currentWindowStart++;
    }

    public boolean isExpired(long timestamp) {
        return System.currentTimeMillis() - timestamp > 1000;
    }
}
