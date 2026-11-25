import java.util.ArrayList;
import java.util.List;

/** Character-flag encoding: keeps per-item status flags in a list. */
public final class FlagCodec {
    private final List<Character> flags = new ArrayList<>();

    public void mark(char flag) {
        if (!flags.contains(flag)) {
            flags.add(flag);
        }
    }

    public void clear(char flag) {
        flags.remove(Character.valueOf(flag));
    }

    public String value() {
        StringBuilder out = new StringBuilder();
        for (char flag : flags) {
            out.append(flag);
        }
        return out.toString();
    }
}
