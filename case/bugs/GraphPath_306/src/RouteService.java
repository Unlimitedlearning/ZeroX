public final class RouteService {
    private final Graph graph;

    public RouteService(int nodes) {
        this.graph = new Graph(nodes);
    }

    public void link(int from, int to, int weight) {
        graph.addEdge(from, to, weight);
    }

    public int edgeWeight(int from, int to) {
        return graph.weight(from, to);
    }

    public PathFinder.Result route(int from, int to) {
        return new PathFinder().shortest(graph, from, to);
    }
}
