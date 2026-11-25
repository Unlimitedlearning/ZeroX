/** 分隔符与空白策略配置。 */
public class CsvConfig {
    public static final char COMMA = ',';
    public static final char QUOTE = '"';

    public static String trimField(String s) {
        return s.trim();
    }
}
