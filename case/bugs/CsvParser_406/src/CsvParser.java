import java.util.ArrayList;
import java.util.List;

/** 最小 CSV 解析器：逗号分隔，双引号包裹含逗号的字段，"" 表示转义引号。 */
public class CsvParser {
    private boolean inQuotes;

    public List<String> parseLine(String line) {
        List<String> fields = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        inQuotes = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (c == '"') {
                if (inQuotes && i + 1 < line.length() && line.charAt(i + 1) == '"') {
                    cur.append('"');
                    i++;
                } else {
                    if (!inQuotes) {
                        inQuotes = true;
                    }
                }
            } else if (c == ',' && !inQuotes) {
                fields.add(cur.toString());
                cur.setLength(0);
            } else {
                cur.append(c);
            }
        }
        fields.add(cur.toString());
        return fields;
    }

    public List<List<String>> parse(String text) {
        List<List<String>> rows = new ArrayList<>();
        for (String line : text.split("\n", -1)) {
            if (!line.isEmpty()) {
                rows.add(parseLine(line));
            }
        }
        return rows;
    }
}
