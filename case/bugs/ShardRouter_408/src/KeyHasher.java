/** 字符串键到整数的哈希。 */
public class KeyHasher {
    public static int of(String key) {
        return HashRing.hash("key:" + key);
    }

    public static int normalize(int h) {
        return h & 0x7ffffffe;
    }
}
