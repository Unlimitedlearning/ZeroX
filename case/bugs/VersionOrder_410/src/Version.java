import java.util.Arrays;

/** 点分版本号：各段按非负整数比较，缺段视为 0。 */
public class Version implements Comparable<Version> {
    private final int[] parts;

    public Version(String text) {
        String[] xs = text.split("\\.");
        parts = new int[xs.length];
        for (int i = 0; i < xs.length; i++) {
            parts[i] = Integer.parseInt(xs[i]);
        }
    }

    public int segmentCount() {
        return parts.length;
    }

    public int segment(int i) {
        return i < parts.length ? parts[i] : 0;
    }

    @Override
    public int compareTo(Version other) {
        int n = Math.max(parts.length, other.parts.length);
        for (int i = 0; i < n; i++) {
            int a = segment(i);
            int b = other.segment(i);
            if (a != b) {
                return Integer.toString(a).compareTo(Integer.toString(b));
            }
        }
        return 0;
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof Version && compareTo((Version) o) == 0;
    }

    @Override
    public int hashCode() {
        return Arrays.hashCode(parts);
    }

    @Override
    public String toString() {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < parts.length; i++) {
            if (i > 0) {
                sb.append('.');
            }
            sb.append(parts[i]);
        }
        return sb.toString();
    }
}
