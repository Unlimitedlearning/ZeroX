final class LruNode {
    final String key;
    int value;
    LruNode prev;
    LruNode next;

    LruNode(String key, int value) {
        this.key = key;
        this.value = value;
    }
}

public final class DList {
    private final LruNode head = new LruNode("#head", 0);
    private final LruNode tail = new LruNode("#tail", 0);
    private int size;

    public DList() {
        head.next = tail;
        tail.prev = head;
    }

    public int size() {
        return size;
    }

    public void addFirst(LruNode node) {
        node.prev = head;
        node.next = head.next;
        head.next.prev = node;
        head.next = node;
        size++;
    }

    public void remove(LruNode node) {
        node.prev.next = node.next;
        node.next.prev = node.prev;
        size--;
    }

    public LruNode removeLast() {
        if (size == 0) {
            return null;
        }
        LruNode victim = tail.prev;
        remove(victim);
        return victim;
    }

    public void moveToHead(LruNode node) {
        if (node.next == tail) {
            return;
        }
        node.prev.next = node.next;
        node.next.prev = node.prev;
        node.next = head.next;
        node.prev = head;
        head.next.prev = node;
        head.next = node;
    }
}
