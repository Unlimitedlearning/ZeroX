/** Adjacency matrix reachability helper. */
public final class AdjMatrix {
    private final boolean[][] cells;

    public AdjMatrix(int nodes) {
        cells = new boolean[nodes][nodes];
    }

    public void link(int from, int to) {
        cells[from][to] = true;
    }

    public boolean reachable(int from, int to) {
        return cells[from][to];
    }

    public int[] neighbors(int node) {
        int count = 0;
        for (boolean cell : cells[node]) {
            if (cell) {
                count++;
            }
        }
        int[] out = new int[count];
        int at = 0;
        for (int i = 0; i < cells[node].length; i++) {
            if (cells[node][i]) {
                out[at++] = i;
            }
        }
        return out;
    }
}
