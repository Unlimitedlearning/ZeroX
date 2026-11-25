public final class PageCursor {
    private final String token;
    private final int offset;

    public PageCursor(String token, int offset) {
        this.token = token;
        this.offset = offset;
    }

    public PageCursor next(int size) {
        return new PageCursor(token, offset + size);
    }

    public int offset() {
        return offset;
    }
}
