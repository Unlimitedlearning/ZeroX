/** 路径片段的词法单元。 */
public class PathToken {
    final String text;
    final boolean wildcard;

    PathToken(String text) {
        this.text = text;
        this.wildcard = "[*]".equals(text);
    }

    public String text() {
        return text;
    }

    public boolean isWildcard() {
        return wildcard;
    }
}
