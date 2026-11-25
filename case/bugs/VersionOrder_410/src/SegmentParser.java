/** 版本号文本切分。 */
public class SegmentParser {
    public static String[] split(String text) {
        return text.split("\\.");
    }

    public static int count(String text) {
        return split(text).length;
    }
}
