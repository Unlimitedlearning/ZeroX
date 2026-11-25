/** 预发布标签比较。 */
public class SemverTag {
    private final String tag;

    public SemverTag(String tag) {
        this.tag = tag;
    }

    public boolean isStable() {
        return tag.isEmpty() || "stable".equals(tag);
    }

    public String value() {
        return tag;
    }
}
