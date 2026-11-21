"""归因卡 / 停滞检测 / 断言提取的纯逻辑自检：不连 WSL、不跑测试。

    python tools/selftests/diagnosis_selftest.py

覆盖：
  1. extract_assertions 识别 ComparisonFailure / TestNG / Hamcrest / XML 实体解码
  2. extract_assertions 不把 expectedTime 之类变量名误判，提取不到返回空
  3. build_failure_card 汇总当前假设、失败用例差分、已排除方向
  4. build_failure_card 对首次失败且无假设/回滚时返回空串
  5. detect_stall 只在失败数+用例集合+补丁位置三者都相同时报警
  6. cap_log_text 拼接超长日志头尾并标注省略（日志安全读取的纯逻辑部分）
  7. detect_hotspot 仅当新增打印落在 sample/parse 等热路径方法时提示，正常修复不打扰
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core.test_output_parser import extract_assertions, cap_log_text  # noqa: E402
from diagnosis import build_failure_card, detect_stall, detect_hotspot  # noqa: E402


def check(cond, message):
    if not cond:
        raise AssertionError(message)


def test_assertions():
    text, n = extract_assertions(
        "junit.framework.ComparisonFailure: expected:<hello> but was:<world>\n"
        "  at org.foo.Bar.test(Bar.java:10)"
    )
    check(n == 1 and "hello" in text and "world" in text, f"ComparisonFailure 未提取: {text}")

    # XML 属性里的 &lt; &gt; 要先解码才能命中尖括号模式
    xml = '<failure message="expected:&lt;1&gt; but was:&lt;2&gt;" type="AssertionError"/>'
    text, n = extract_assertions(xml)
    check(n == 1 and "'1'" in text and "'2'" in text, f"XML 实体未解码: {text}")

    text, n = extract_assertions("expected [A] but found [B]")
    check(n == 1 and "A" in text and "B" in text, f"TestNG 格式未提取: {text}")

    text, n = extract_assertions("Expected: is <3>\n     but: was <4>")
    check(n == 1 and "3" in text and "4" in text, f"Hamcrest 格式未提取: {text}")

    text, n = extract_assertions("long expectedTime, actualTime; assertEquals(a, b);")
    check(n == 0 and text == "", f"变量名不应误判: {text}")

    # 去重：同一对(expected, actual)出现两次只留一条
    text, n = extract_assertions(
        "expected:<x> but was:<y>\nexpected:<x> but was:<y>"
    )
    check(n == 1, f"重复断言应去重: n={n}")


def _patch(round_no, path):
    return {"t": "patch", "round": round_no, "reasoning": f"假设{round_no}",
            "files": [{"path": path}]}


def _test(round_no, status, count, detail):
    return {"t": "test", "round": round_no, "status": status,
            "fail_count": count, "detail": detail}


def test_failure_card():
    events = [
        _patch(1, "/w/src/Foo.java"),
        _test(1, "Failed", 3, "- A::t1\n- A::t2\n- A::t3"),
        {"t": "rollback", "target_round": 1, "kind": "probe", "reasoning": "探针无效"},
        _patch(2, "/w/src/Foo.java"),
        _test(2, "Failed", 2, "- A::t1\n- A::t2"),
    ]
    card = build_failure_card(events)
    check("当前假设（轮 2）" in card, f"缺当前假设: {card}")
    check("消失 A::t3" in card and "失败数 3 → 2" in card, f"缺失败差分: {card}")
    check("已排除（探针" in card and "探针无效" in card, f"缺已排除方向: {card}")

    # 首次失败、无假设无回滚 → 空串（首次失败本身仍给一条简要说明）
    first_only = [_test(0, "Failed", 1, "- A::t1")]
    card = build_failure_card(first_only)
    check("首次失败" in card and "当前假设" not in card, f"首次失败卡片不符: {card}")
    check(build_failure_card([]) == "", "空事件流应返回空串")


def test_stall():
    d3 = "- A::t1\n- A::t2\n- A::t3"
    # 两轮改同一文件、失败数与用例集合都不变 → 报警
    stalled = [
        _patch(1, "/w/src/main/java/Foo.java"),
        _test(1, "Failed", 3, d3),
        _patch(2, "/w/src/main/java/Foo.java"),
        _test(2, "Failed", 3, d3),
    ]
    hint = detect_stall(stalled)
    check("停滞提示" in hint and "Foo.java" in hint, f"应报警未报: {hint}")

    # 失败数变了 → 不报（有进展）
    progressed = [
        _patch(1, "/w/src/Foo.java"), _test(1, "Failed", 3, d3),
        _patch(2, "/w/src/Foo.java"), _test(2, "Failed", 2, "- A::t1"),
    ]
    check(detect_stall(progressed) == "", "失败数下降不应报停滞")

    # 用例集合变了(修好一个又暴露一个) → 不报
    churn = [
        _patch(1, "/w/src/Foo.java"), _test(1, "Failed", 3, d3),
        _patch(2, "/w/src/Foo.java"), _test(2, "Failed", 3, "- A::t1\n- A::t4\n- A::t5"),
    ]
    check(detect_stall(churn) == "", "用例集合变化不应报停滞")

    # 改动文件不同 → 不报
    other_file = [
        _patch(1, "/w/src/Foo.java"), _test(1, "Failed", 3, d3),
        _patch(2, "/w/src/Bar.java"), _test(2, "Failed", 3, d3),
    ]
    check(detect_stall(other_file) == "", "改动文件不同不应报停滞")

    # 只有一次失败 → 不报
    check(detect_stall([_patch(1, "/w/src/Foo.java"), _test(1, "Failed", 3, d3)]) == "",
          "单次失败不应报停滞")


def test_cap_log_text():
    text = cap_log_text("HEAD-CONTENT", "TAIL-CONTENT", 451 * 1024 * 1024)
    check("HEAD-CONTENT" in text and "TAIL-CONTENT" in text, f"头尾应都保留: {text[:80]}")
    check("已省略" in text and "451.0MB" in text, f"缺省略标记/体积提示: {text[:120]}")
    check(text.index("HEAD-CONTENT") < text.index("已省略") < text.index("TAIL-CONTENT"),
          "头/省略标记/尾的顺序不对")


def test_hotspot():
    # 行号是改动前的原文行号：sample 在 2-5 行，getName 在 7-10 行
    src = (
        "public class Foo {\n"
        "    public double sample(int x) {\n"
        "        double r = Math.random();\n"
        "        return r * x;\n"
        "    }\n"
        "    public String getName() {\n"
        "        return \"foo\";\n"
        "    }\n"
        "}\n"
    )
    # 正例：打印新增在 sample() 内 → 提示
    hint = detect_hotspot(src, [(3, 3, 'System.out.println("probe:" + r);')])
    check("热路径提示" in hint and "sample" in hint, f"热路径打印应提示: {hint}")

    # 正例：logger 调用也算打印特征
    hint2 = detect_hotspot(src, [(3, 3, "LOGGER.debug(\"x\");")])
    check("热路径提示" in hint2, f"logger 打印应提示: {hint2}")

    # 反例：同样在 sample() 内，但改的是正常逻辑（无打印）→ 不打扰
    check(detect_hotspot(src, [(4, 4, "return r * x + 1;")]) == "",
          "无新增打印的正常修复不应提示")

    # 反例：有打印，但落在非热点的 getName() → 不提示
    check(detect_hotspot(src, [(8, 8, 'System.out.println("x");')]) == "",
          "非热路径方法的打印不应提示")

    # 反例：空 changes / 空源码 → 不提示
    check(detect_hotspot(src, []) == "", "无改动不应提示")
    check(detect_hotspot("", [(3, 3, "System.out.println")]) == "", "空源码不应提示")


def main():
    test_assertions()
    test_failure_card()
    test_stall()
    test_cap_log_text()
    test_hotspot()
    print("diagnosis selftest: OK")


if __name__ == "__main__":
    main()
