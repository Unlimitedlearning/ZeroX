import java.util.ArrayList;
import java.util.List;

/** 版本区间：按 compareTo 判定边界，提供筛选与排序。 */
public class VersionRange {
    private final Version low;
    private final Version high;

    public VersionRange(String low, String high) {
        this.low = new Version(low);
        this.high = new Version(high);
    }

    public boolean contains(String v) {
        Version x = new Version(v);
        return x.compareTo(low) >= 0 && x.compareTo(high) <= 0;
    }

    public List<String> sortStrings(List<String> vs) {
        List<Version> xs = new ArrayList<>();
        for (String s : vs) {
            xs.add(new Version(s));
        }
        xs.sort(null);
        List<String> out = new ArrayList<>();
        for (Version x : xs) {
            out.add(x.toString());
        }
        return out;
    }
}
