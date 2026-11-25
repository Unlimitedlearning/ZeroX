import java.util.BitSet;

/** Bit-set wrapper exposing set / clear / has. */
public final class BitSetPool {
    private final BitSet bits = new BitSet();

    public void set(int bit) {
        bits.set(bit);
    }

    public void clear(int bit) {
        bits.clear(bit);
    }

    public boolean has(int bit) {
        return bits.get(bit);
    }
}
