"""失败归因卡与停滞检测：从 work_log 事件流本地生成，不耗模型对话轮。

输入是 worklog.WorkLog.events() 的事件列表（纯数据），输出是可直接附到 run_test
失败返回里的文本块。两者都可能返回空串——提取不到就退回原有返回，绝不误报。

放这里而不是 worklog.py：worklog 是「最小版本控制系统」，不该知道失败用例、
补丁停滞这些 APR 语义；而 server 只负责读事件 + 拼 surface，判定逻辑集中在此。
"""
import re

from zerox_core.search import parse_failing_tests, extract_java_structure


# 高频调用路径的方法名片段（小写比对）：改动落在这里且新增了打印，输出会被放大。
_HOT_METHOD_TOKENS = (
    "sample", "random", "next", "parse", "format", "iterate", "loop",
    "cumulative", "probability", "density",
)
# 新增行里的调试输出特征（ System.out/err、各种 print、日志调用 ）。
_PRINT_CALL = re.compile(
    r"System\s*\.\s*(?:out|err)|\.print(?:ln|f)?\s*\(|printStackTrace\s*\(|\blog(?:ger)?\s*\.\s*\w+\s*\(",
    re.I,
)


def _basename(path):
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1]


def _cases(detail):
    """从 test 事件的 detail 里取失败用例集合 {(类全名, 方法名)}。"""
    return set(parse_failing_tests(detail))


def _patch_paths(event):
    return {_basename(item.get("path")) for item in (event.get("files") or [])
            if item.get("path")}


def _clip(text, limit):
    flat = " ".join(str(text or "").split())
    return flat[:limit] + "…" if len(flat) > limit else flat


def _case_list(cases, limit=3):
    return ", ".join(f"{cls}::{method}" for cls, method in sorted(cases)[:limit])


def build_failure_card(events, max_chars=720):
    """汇总三件事，让模型不必翻日志就能判断「该微调还是该换方向」。

    1. 当前假设：最近一条存活 patch 的 reasoning（被回滚的已不在 events 里）。
    2. 失败差异：最近两次 Failed 之间失败用例集合的新增/消失 + 失败数变化。
       只列用例集合差分，不重复整段报错（报错已在 fail_msg 里）。
    3. 已排除方向：最近一条 rollback 的 kind + reasoning。

    只在能给出至少一条实质信息时返回文本；纯首次失败且无假设/回滚时返回空串。
    """
    evs = list(events or [])
    failed = [(idx, ev) for idx, ev in enumerate(evs)
              if ev.get("t") == "test" and ev.get("status") == "Failed"]
    patches = [ev for ev in evs if ev.get("t") == "patch"]
    rollbacks = [ev for ev in evs if ev.get("t") == "rollback"]

    lines = ["【失败归因卡（本地汇总）】"]

    if patches:
        latest = patches[-1]
        why = _clip(latest.get("reasoning"), 180)
        if why:
            lines.append(f"当前假设（轮 {latest.get('round')}）：{why}")

    if failed:
        _idx, latest = failed[-1]
        count = latest.get("fail_count")
        cur = _cases(latest.get("detail"))
        if len(failed) >= 2:
            _prev_idx, prev = failed[-2]
            old = _cases(prev.get("detail"))
            added, gone = cur - old, old - cur
            parts = []
            if added:
                parts.append("新增 " + _case_list(added))
            if gone:
                parts.append("消失 " + _case_list(gone))
            if not parts:
                parts.append("失败用例集合未变")
            prev_count = prev.get("fail_count")
            if isinstance(prev_count, int) and isinstance(count, int):
                parts.append(f"失败数 {prev_count} → {count}")
            lines.append("本轮失败差异：" + "；".join(parts))
        else:
            summary = f"{count if count is not None else '若干'} 个用例"
            if cur:
                summary += "：" + _case_list(cur)
            lines.append("首次失败：" + summary)

    if rollbacks:
        rb = rollbacks[-1]
        label = {"probe": "探针", "deadend": "方向失败"}.get(
            rb.get("kind"), rb.get("kind") or "回滚")
        why = _clip(rb.get("reasoning"), 140)
        lines.append(f"已排除（{label}，回滚到轮 {rb.get('target_round')}）："
                     + (why or "未记录理由"))

    if len(lines) == 1:
        return ""
    text = "\n".join(lines)
    return text[:max_chars] + "…" if len(text) > max_chars else text


def _nearest_patch(evs, before_idx):
    """取 before_idx 之前最近的一条 patch；没有则返回 None。"""
    for ev in reversed(evs[:before_idx]):
        if ev.get("t") == "patch":
            return ev
    return None


def detect_stall(events, max_chars=260):
    """连续两轮「失败数相同 + 失败用例集合相同 + 改动文件相同」时返回停滞提示。

    三个条件同时成立才报：只凭失败数相同会误伤「修好一个又暴露一个」的进展，
    只凭文件相同会误伤大文件里换了方法的情况。失败用例集合用 Class::method 比对，
    补丁位置用文件 basename 比对——两者都是本地已有的数据，不额外读文件。
    """
    evs = list(events or [])
    failed = [(idx, ev) for idx, ev in enumerate(evs)
              if ev.get("t") == "test" and ev.get("status") == "Failed"]
    if len(failed) < 2:
        return ""

    i1, t1 = failed[-2]
    i2, t2 = failed[-1]
    c1, c2 = t1.get("fail_count"), t2.get("fail_count")
    if not isinstance(c1, int) or c1 != c2:
        return ""

    cases1, cases2 = _cases(t1.get("detail")), _cases(t2.get("detail"))
    if not cases1 or cases1 != cases2:
        return ""

    p1, p2 = _nearest_patch(evs, i1), _nearest_patch(evs, i2)
    if p1 is None or p2 is None or p1 is p2:
        return ""
    paths1, paths2 = _patch_paths(p1), _patch_paths(p2)
    if not paths1 or paths1 != paths2:
        return ""
    if (p2.get("round") or 0) <= (p1.get("round") or 0):
        return ""

    text = ("【停滞提示（本地判定）】连续两轮改动 " + ", ".join(sorted(paths1))
            + f" 后，仍是同一组 {c1} 个失败用例。当前方向大概率无效："
            "建议 rollback 撤销这两轮并换假设，或先核对失败的断言原文再动手。")
    return text[:max_chars] + "…" if len(text) > max_chars else text


def detect_hotspot(source, changes, max_chars=340):
    """新增打印落在高频调用方法里时，返回一句客观位置提示（否则空串）。

    source 传改动前的原文（锚点行号基于它），changes 是 (start_line, end_line,
    new_text) 的可迭代。只陈述代码事实——新增行含打印调用 + 该行位于 sample/parse/
    循环类方法内；不判断是否有探针、不打探针标记。正常修复（无新增打印）不打扰。
    这样能提前拦住「探针打进解析热路径刷屏 451MB 日志拖垮 JVM」这类事故。
    """
    try:
        items, _truncated = extract_java_structure(source)
    except Exception:
        return ""
    methods = [it for it in (items or [])
               if it.get("kind") in ("method", "constructor")]
    if not methods:
        return ""

    hits, seen = [], set()
    for start, end, new_text in (changes or []):
        if not _PRINT_CALL.search(str(new_text or "")):
            continue
        try:
            s, e = int(start), int(end or start)
        except (TypeError, ValueError):
            continue
        for m in methods:
            if m["line_end"] < s or m["line_start"] > e:
                continue
            # 方法范围可能嵌套（外层方法含匿名/内部类方法），不因一个非热点就停，
            # 遍历全部重叠项，命中任一热点即记录；seen 保证不重复。
            sig = m.get("signature", "")
            name = sig.split("(", 1)[0].lower()
            if any(tok in name for tok in _HOT_METHOD_TOKENS) and sig not in seen:
                seen.add(sig)
                hits.append((sig, s))
    if not hits:
        return ""

    detail = ", ".join(f"{sig}（约第 {line} 行）" for sig, line in hits[:3])
    text = ("【热路径提示（本地位置判定）】新增的打印语句位于高频调用方法 " + detail
            + "。这类方法在测试中可能被反复调用，打印会被放大成海量日志甚至拖垮 JVM；"
            + "建议在 run_test 前移除调试输出，或改到只执行一次的位置。")
    return text[:max_chars] + "…" if len(text) > max_chars else text
