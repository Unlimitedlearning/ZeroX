import java.util.ArrayList;
import java.util.List;

public final class Tokenizer {
    public List<String> tokenize(String expr) {
        List<String> out = new ArrayList<>();
        int i = 0;
        while (i < expr.length()) {
            char c = expr.charAt(i);
            if (Character.isWhitespace(c)) {
                i++;
                continue;
            }
            if (Character.isDigit(c)) {
                int j = i;
                while (j < expr.length() && Character.isDigit(expr.charAt(j))) {
                    j++;
                }
                out.add(expr.substring(i, j));
                i = j;
                continue;
            }
            out.add(String.valueOf(c));
            i++;
        }
        return out;
    }
}
