public final class RemoteStub {
    private int calls;
    private final boolean result;

    public RemoteStub(boolean result) {
        this.result = result;
    }

    public boolean invoke() {
        calls++;
        return result;
    }

    public int calls() {
        return calls;
    }
}
