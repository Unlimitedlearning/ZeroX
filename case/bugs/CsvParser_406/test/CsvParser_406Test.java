import java.util.List;

public class CsvParser_406Test {

    interface Case {
        String run();
    }

    static String check(String label, boolean ok, String detail) {
        return ok ? null : label + " -> " + detail;
    }

    static String execute(Case c) {
        try {
            return c.run();
        } catch (Throwable t) {
            return t.getClass().getSimpleName() + ": " + t.getMessage();
        }
    }

    static String line(String s) {
        return String.join("|", new CsvParser().parseLine(s));
    }

    public static void main(String[] args) {
        String[] names = {
            "plain fields split",
            "empty field preserved",
            "quoted comma stays inside",
            "field after quoted comma splits",
            "quoted field at end",
            "escaped quote inside field",
            "escaped quote then next field",
            "two quoted fields",
            "multiline rows",
            "blank line skipped",
            "index helper is independent",
            "config trims independently"
        };
        String[] results = new String[names.length];

        results[0] = execute(() ->
            check("plain", "a|b|c".equals(line("a,b,c")), "got=" + line("a,b,c")));

        results[1] = execute(() ->
            check("empty", "a||c".equals(line("a,,c")), "got=" + line("a,,c")));

        results[2] = execute(() ->
            check("inside", "a,b".equals(line("\"a,b\"")), "got=" + line("\"a,b\"")));

        results[3] = execute(() ->
            check("after", "a,b|c".equals(line("\"a,b\",c")), "got=" + line("\"a,b\",c")));

        results[4] = execute(() ->
            check("at end", "x|a,b".equals(line("x,\"a,b\"")), "got=" + line("x,\"a,b\"")));

        results[5] = execute(() ->
            check("escaped", "a\"b".equals(line("\"a\"\"b\"")), "got=" + line("\"a\"\"b\"")));

        results[6] = execute(() ->
            check("escaped next", "a\"b|c".equals(line("\"a\"\"b\",c")), "got=" + line("\"a\"\"b\",c")));

        results[7] = execute(() ->
            check("two quoted", "a,b|c,d".equals(line("\"a,b\",\"c,d\"")),
                "got=" + line("\"a,b\",\"c,d\"")));

        results[8] = execute(() -> {
            List<List<String>> rows = new CsvParser().parse("a,b\nc,d");
            return check("multiline", rows.size() == 2
                    && "a|b".equals(String.join("|", rows.get(0)))
                    && "c|d".equals(String.join("|", rows.get(1))), "rows=" + rows);
        });

        results[9] = execute(() -> {
            List<List<String>> rows = new CsvParser().parse("a,b\n\nc,d");
            return check("blank", rows.size() == 2, "rows=" + rows.size());
        });

        results[10] = execute(() -> {
            FieldIndex idx = new FieldIndex(java.util.Arrays.asList("p", "q"));
            return check("index", idx.width() == 2 && "q".equals(idx.at(1))
                    && "".equals(idx.at(9)), "index off");
        });

        results[11] = execute(() ->
            check("trim", "x".equals(CsvConfig.trimField("  x  "))
                    && CsvConfig.COMMA == ',' && CsvConfig.QUOTE == '"', "config off"));

        int failed = 0;
        for (int i = 0; i < names.length; i++) {
            if (results[i] == null) {
                System.out.println("[PASS] " + names[i]);
            } else {
                failed++;
                System.out.println("[FAIL] " + names[i] + ": " + results[i]);
            }
        }
        System.out.println();
        System.out.println((names.length - failed) + "/" + names.length + " test cases passed");
        if (failed == 0) {
            System.out.println("All tests passed");
        } else {
            System.out.println("FAILED (" + failed + ")");
        }
    }
}
