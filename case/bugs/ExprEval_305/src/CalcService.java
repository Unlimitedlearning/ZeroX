public final class CalcService {
    private final ExprEvaluator evaluator = new ExprEvaluator();

    public int eval(String expression) {
        return evaluator.evaluate(expression);
    }
}
