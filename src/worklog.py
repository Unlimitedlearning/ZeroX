"""worklog —— 给 agent 用的最小版本控制系统（与 APR 无关，可单独复用）。

三个概念：
  blob    文件内容按 sha1 存一份，和路径无关，天然去重，也不会被同名文件覆盖
  event   一条 JSON 事件（patch / test / rollback），append-only
  round   一轮修复；rollback(to_round) 撤销该轮及之后的全部 patch

设计取舍：
  1. 目标文件的读写通过 io 回调注入：本地文件、WSL 工作区、远端容器跑同一份代码。
  2. 事件只能追加：失败数是在收敛还是在发散，是 agent 真正需要的那条信号，
     所以 test 事件永不覆盖。
  3. patch 事件自带 before/after 的 blob 引用：before=null 表示新建文件，回滚即删除。

模型的 think 原文单独放在 thoughts.jsonl（一行一条），不进 work_log.jsonl：
它不是版本事件，回滚不该删掉它——被撤销方向的思考恰恰是压缩后要保留的线索。

日志格式（work_log.jsonl，一行一条）。正常路径只追加、永不覆盖写；唯一的例外是
rollback()：它先把被撤销的事件抄进 human_audit.jsonl，再重写 work_log.jsonl（见下方 rollback）：
  {"t":"patch","ts":"2026-11-17T14:12:05","round":3,"status":"PATCHED",
   "reasoning":"...","summary":"...",
   "files":[{"path":"/ws/src/Foo.java","before":"<sha1>"|null,"after":"<sha1>",
             "added":4,"removed":2}]}
  {"t":"test","ts":"...","round":3,"status":"Failed","fail_count":5,"detail":"..."}
  {"t":"rollback","ts":"...","target_round":3,"kind":"probe"|"deadend","reasoning":"..."}

rollback 事件故意不带 round 字段：rollback() 重写日志时会丢弃 round >= target 的事件，
带了 round 的回滚记录会被自己这次回滚删掉，后续更低的 target 也会连带删掉更早的记录。
"""
import datetime
import difflib
import hashlib
import json
import os
import sys


class WorkLogError(Exception):
    """落盘失败。restored / broken 告诉调用方哪些文件已经还原、哪些没救回来。"""

    def __init__(self, message, restored=None, broken=None):
        super().__init__(message)
        self.restored = restored or []
        self.broken = broken or []


class LocalIO:
    """默认 IO：直接读写本机文件。newline="" 保证换行符原样返回/写回，不被平台改写。"""

    def read(self, path):
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            return f.read()

    def write(self, path, content):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)

    def remove(self, path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _line_delta(before, after):
    """只数增删行数，用来在 digest 里给出一眼可见的改动规模。"""
    added = removed = 0
    for line in difflib.unified_diff((before or "").splitlines(), (after or "").splitlines(), lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _round_reached(round_value, target):
    """round >= target，任一值不是整数就当不满足（坏数据不该触发回滚）。"""
    try:
        return int(round_value) >= int(target)
    except (TypeError, ValueError):
        return False


def _stamp(ts):
    """ISO 时间 → HH:MM:SS，digest 里只需要这个精度。"""
    return ts[11:19] if isinstance(ts, str) and len(ts) >= 19 else (ts or "")


def _clip(text, limit):
    """单行截断，保留开头：reasoning 的前半句通常就是假设本身。"""
    text = str(text or "").strip().replace("\n", " ")
    if limit and len(text) > limit:
        return text[:limit] + "…"
    return text


def _test_line(event, previous_fail):
    """一行测试结论；顺带把「比上轮更好还是更差」写出来，这是判断该微调还是该换方向的关键。"""
    text = f"测试 {event.get('status') or '?'}"
    count = event.get("fail_count")
    if count is None:
        return text
    text += f"：{count} 个失败"
    if isinstance(previous_fail, int) and isinstance(count, int):
        if count < previous_fail:
            text += f"（上轮 {previous_fail} → 好转 {previous_fail - count}）"
        elif count > previous_fail:
            text += f"（上轮 {previous_fail} → 变差 {count - previous_fail}）"
        else:
            text += "（与上轮持平）"
    return text


# 回滚原因的分类只在渲染时用；kind 本身是自由字符串，别的 agent 可以自定义
_ROLLBACK_LABELS = {"probe": "探针", "deadend": "方向失败"}


class WorkLog:
    """一个目录 = 一个版本库：home 下同时放 log、audit 和 blobs。"""

    def __init__(self, home, io=None, test_tail=2000):
        self.home = home
        self.blob_dir = os.path.join(home, "blobs")
        self.log_path = os.path.join(home, "work_log.jsonl")
        self.audit_path = os.path.join(home, "human_audit.jsonl")
        self.thought_path = os.path.join(home, "thoughts.jsonl")
        self.io = io or LocalIO()
        self.test_tail = test_tail

    def reset_events(self):
        """清空本运行的推导痕迹：事件日志（work_log.jsonl）与 think（thoughts.jsonl）。

        开跑新任务时调用：首轮 API 请求在 run_test（进而服务端 checkout 清日志）之前发出，
        若沿用上一次运行的历史，L1 状态卡会把上次的补丁与「测试全部通过」当成当前状态
        喂给模型，等于把答案泄露出去。thoughts 同样要清，否则 brief() 会因它非空而继续
        渲染上次的思考节选。
        保留 human_audit.jsonl（跨运行的事后审计归档）与 blobs/（按内容寻址，残留不冲突），
        只清与「本次运行」一一对应的那两份。
        """
        self.io.remove(self.log_path)
        self.io.remove(self.thought_path)

    # ---------------- 写 ----------------

    def note(self, turn, text):
        """把模型本轮 think 原文落盘（客户端唯一持有，压缩后据此恢复当时假设）。

        只在客户端调用；服务端不写这个文件，所以 rollback 重写 work_log 时不会碰到它。
        空文本不落盘：没思考就没有可恢复的假设，写一行空记录只会稀释后续读取。
        """
        text = str(text or "").strip()
        if not text:
            return None
        event = {"t": "thought", "ts": _now(), "turn": turn, "text": text}
        os.makedirs(self.home, exist_ok=True)
        with open(self.thought_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def apply(self, round, status, reasoning, summary, writes):
        """提交一轮 patch。

        writes: [(path, before, after)]；before 为 None 表示新建文件。
        原子性：先把新旧内容全部落成 blob，再逐个写目标文件；任何一步失败就按 before 还原。
        """
        os.makedirs(self.blob_dir, exist_ok=True)

        files = []
        for path, before, after in writes:
            added, removed = _line_delta(before, after)
            files.append({
                "path": path,
                "before": self._put(before) if before is not None else None,
                "after": self._put(after),
                "added": added,
                "removed": removed,
            })

        applied = []
        try:
            for path, before, after in writes:
                self.io.write(path, after)
                applied.append((path, before))
        except Exception as err:
            restored, broken = self._restore(applied)
            raise WorkLogError(f"{err}", restored, broken) from err

        event = {
            "t": "patch",
            "ts": _now(),
            "round": round,
            "status": status,
            "reasoning": reasoning,
            "summary": summary,
            "files": files,
        }
        self._append(event)
        return event

    def test(self, round, status, fail_count=None, detail=""):
        """追加一条测试结论（永不删除、永不覆盖历史）。"""
        event = {
            "t": "test",
            "ts": _now(),
            "round": round,
            "status": status,
            "fail_count": fail_count,
            "detail": (detail or "")[-self.test_tail:],
        }
        self._append(event)
        return event

    def rollback(self, to_round, reasoning="", kind="deadend"):
        """撤销 >= to_round 的全部 patch，并把被撤销的事件归档到 human_audit.jsonl。

        返回 (已恢复文件列表, 还原失败文件列表)。一个文件都没恢复就完全不动日志，
        避免把一次失败的回滚变成一次静默的数据丢失。

        reasoning / kind 给这次回滚留一条「为什么退回来」的记录（探针无效 or 方向失败）：
        被撤销的那几轮连同它们的 reasoning 一起进了 human_audit.jsonl，agent 之后看不到，
        所以这条记录必须在重写之后写进幸存的 work_log.jsonl。
        """
        events = self.events()
        dropped = [ev for ev in events if ev["t"] == "patch" and _round_reached(ev.get("round"), to_round)]

        restored, broken = [], []
        # 倒序撤销：同一个文件被改过多次时，最后一次写入前的 before 才是原始内容
        for event in reversed(dropped):
            for item in reversed(event.get("files") or []):
                path = item.get("path")
                if not path:
                    continue
                try:
                    if item.get("before"):
                        self.io.write(path, self._get(item["before"]))
                    else:
                        self.io.remove(path)  # before 为空 = 这一轮新建的文件
                    restored.append(path)
                except Exception as err:
                    broken.append(path)
                    self._warn(f"恢复 {path} 失败: {err}")

        if not restored:
            return [], broken

        kept = [ev for ev in events if not _round_reached(ev.get("round"), to_round)]
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                for event in events:
                    if _round_reached(event.get("round"), to_round):
                        archived = dict(event)
                        archived["rollback_target"] = to_round
                        f.write(json.dumps(archived, ensure_ascii=False) + "\n")
            with open(self.log_path, "w", encoding="utf-8") as f:
                for event in kept:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError as err:
            self._warn(f"重写 work_log 失败: {err}")

        # 必须写在重写之后：带 round 的话会被上面的 kept 过滤当成「被撤销的事件」删掉，
        # 所以只记 target_round —— _round_reached(None, ...) 恒为 False，后续回滚也删不掉它。
        self._append({
            "t": "rollback",
            "ts": _now(),
            "target_round": to_round,
            "kind": (kind or "deadend"),
            "reasoning": (reasoning or "").strip(),
        })

        return restored, broken

    # ---------------- 读 ----------------

    def thoughts(self, limit=3):
        """按写入顺序返回最近 limit 条 think 原文（压缩后回传给模型的那几条）。

        坏行跳过；文件不存在时视为空。不在这里截断 text —— 调用方决定给模型多少字。
        """
        if not os.path.exists(self.thought_path):
            return []
        events = []
        with open(self.thought_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("t") == "thought":
                    events.append(event)
        return events[-limit:] if limit and limit > 0 else events

    def events(self):
        """按写入顺序读出 patch/test/rollback 事件；坏行和已废弃事件跳过。"""
        if not os.path.exists(self.log_path):
            return []
        result = []
        allowed = {"patch", "test", "rollback"}
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("t") in allowed:
                    result.append(event)
        return result

    def digest(self, limit=5, title=None, detail_chars=1200, reasoning_chars=400):
        """给 agent 看的紧凑视图：每轮改了什么、为什么改、测试怎么变的。

        只给摘要，不回放 old/new 全文 —— 那是让模型花钱读自己刚写过的代码。
        reasoning 是压缩后唯一能找回「当时怎么想」的东西，必须回读。
        """
        events = self.events()
        who = f"当前 Bug（{title}）" if title else ""
        if not events:
            return f"{who}暂无历史工作日志。"

        groups = []
        for event in events:
            if event["t"] == "patch":
                groups.append({"round": event.get("round"), "patch": event, "tests": []})
            elif event["t"] == "rollback":
                # 回滚独立成组：它撤掉的轮次已经不在日志里了，挂在别的组下面会被读成那一轮的一部分
                groups.append({"round": None, "patch": None, "tests": [], "rollback": event})
            elif groups:
                groups[-1]["tests"].append(event)
            else:
                groups.append({"round": event.get("round"), "patch": None, "tests": [event]})

        shown = groups[-limit:] if isinstance(limit, int) and limit > 0 else groups

        # 每个测试事件对应的「上一次失败数」，用于渲染好转/变差
        previous, previous_map = None, {}
        for event in events:
            if event["t"] == "test":
                previous_map[id(event)] = previous
                previous = event.get("fail_count")

        # 「轮」只数带 patch 的组：回滚和开头的孤立测试都不是一轮，计进去会把表头抬得比实际高
        round_count = len([g for g in groups if g.get("patch")])
        rollback_count = self._count(events, "rollback")
        lines = [
            f"{who}最近 {len(shown)} 条记录"
            f"（共 {round_count} 轮 / {self._count(events, 'patch')} 个补丁 / {self._count(events, 'test')} 次测试"
            + (f" / {rollback_count} 次回滚" if rollback_count else "")
            + "）"
        ]
        # 压缩后第一眼要看的不是「改了哪些行」，而是「当时为什么这么改」。
        # 取最后一条存活 patch（回滚掉的已被 rollback 从 events 里删掉），再在下面按轮展开。
        latest_patch = next((ev for ev in reversed(events) if ev["t"] == "patch"), None)
        if latest_patch and reasoning_chars:
            # 顶部只放短版，完整 reasoning 在下面对应轮次里，避免每次读日志都重复一遍
            why = _clip(latest_patch.get("reasoning"), min(160, reasoning_chars))
            if why:
                lines.append(f"当前推断（轮 {latest_patch.get('round')}）：{why}")
        for group in shown:
            patch = group["patch"]
            rollback_event = group.get("rollback")
            head_event = patch or rollback_event or (group["tests"] or [{}])[0]
            if rollback_event:
                head = f"[回滚] {_stamp(head_event.get('ts'))}"
            elif group.get("round") is None:
                # 第一轮之前的测试没有轮号，别渲染成「轮 None」
                head = f"[记录] {_stamp(head_event.get('ts'))}"
            else:
                head = f"[轮 {group.get('round')}] {_stamp(head_event.get('ts'))}"
            if patch:
                summary = (patch.get("summary") or "").strip()
                lines.append(f"{head} {patch.get('status') or 'PATCH'}  {summary}".rstrip())
                why = _clip(patch.get("reasoning"), reasoning_chars)
                if why:
                    lines.append(f"        推断：{why}")
                for item in patch.get("files") or []:
                    name = str(item.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
                    delta = ""
                    if item.get("after") is not None:
                        delta = f" +{item.get('added', 0)}/-{item.get('removed', 0)}"
                    lines.append(f"        {name}{delta}")
            elif rollback_event:
                kind = rollback_event.get("kind") or ""
                label = _ROLLBACK_LABELS.get(kind, kind or "回滚")
                lines.append(
                    f"{head} 撤销到第 {rollback_event.get('target_round')} 轮（{label}）"
                )
                why = _clip(rollback_event.get("reasoning"), reasoning_chars)
                if why:
                    lines.append(f"        推断：{why}")
            else:
                lines.append(head)
            for event in group["tests"]:
                lines.append("        " + _test_line(event, previous_map.get(id(event))))

        latest = [ev for ev in events if ev["t"] == "test"]
        if latest and detail_chars:
            detail = (latest[-1].get("detail") or "").strip()
            if detail:
                lines.append("")
                lines.append(f"--- 最近一次测试详情（尾 {detail_chars} 字）---")
                lines.append(detail[-detail_chars:])
        return "\n".join(lines)

    def brief(self, max_chars=1200, title=None):
        """压缩后常驻注入上下文的精简快照：当前推断 / 失败数趋势 / 已排除方向。

        与 digest() 的区别：digest 是模型主动调用 get_work_log 时才看到的完整视图；
        brief 是压缩那一刻由客户端主动写回上下文的一段状态，恢复不再只靠模型「记得去读日志」。
        总长度受 max_chars 约束，避免摘要本身又把刚压缩出来的窗口撑大。
        """
        events = self.events()
        who = f"当前 Bug（{title}）" if title else "当前任务"
        thoughts = self.thoughts(3)
        if not events and not thoughts:
            return f"{who}：暂无历史工作日志。"

        patches = [e for e in events if e["t"] == "patch"]
        tests = [e for e in events if e["t"] == "test"]
        rollbacks = [e for e in events if e["t"] == "rollback"]

        lines = [f"{who}·状态摘要（共 {len(patches)} 轮补丁 / {len(tests)} 次测试"
                 + (f" / {len(rollbacks)} 次回滚" if rollbacks else "") + "）"]

        # 最近三条 think 的原文节选。补丁前的假设只存在于 think 里，压缩若把它们丢掉，
        # 模型会在空状态上重新推理一遍（Chart_13 实测）。完整原文由客户端在压缩提示中回传。
        if thoughts:
            lines.append("最近思考（原文节选，完整见 thoughts.jsonl）：")
            for note in thoughts:
                lines.append(f"  [轮 {note.get('turn')}] " + _clip(note.get("text"), 180))

        # 当前假设：最后一条存活补丁的推理（回滚掉的已不在 events 里）
        latest = next((e for e in reversed(patches)), None)
        if latest:
            lines.append(f"当前推断（轮 {latest.get('round')}）："
                         + (_clip(latest.get("reasoning"), 220) or "（未记录）"))
            summary = _clip(latest.get("summary"), 120)
            if summary:
                lines.append(f"本轮动作：{summary}")

        # 失败数趋势：首 → 末，是判断收敛还是发散的关键信号。
        # 只有一次测试时不写成「N → N」：那读起来像「补丁没生效」，模型会据此重打一遍。
        if tests:
            first, last = tests[0], tests[-1]
            last_round = last.get("round") or 0
            if len(tests) >= 2:
                lines.append(f"测试趋势：{first.get('fail_count', '?')} → "
                             f"{last.get('fail_count', '?')} 个失败（{last.get('status', '?')}）")
            else:
                lines.append(f"上次测试（轮 {last_round}）：{last.get('fail_count', '?')} 个失败"
                             f"（{last.get('status', '?')}）")
            # 补丁已打上但还没测：必须点明「下面是打补丁之前的数字」，
            # 否则状态卡会被读成「刚打的补丁仍然失败」。
            untested = [p for p in patches if (p.get("round") or 0) > last_round]
            if untested:
                rounds = "、".join(str(p.get("round")) for p in untested)
                lines.append(f"未验证：轮 {rounds} 的补丁还没跑过测试，以上是它之前的结果")
            detail = (last.get("detail") or "").strip()
            if detail:
                last_line = detail.splitlines()[-1] if detail.splitlines() else detail
                lines.append("最近报错：" + _clip(last_line, 180))

        # 已排除方向：回滚理由，防止压缩后重复走死路
        for rb in rollbacks[-3:]:
            label = _ROLLBACK_LABELS.get(rb.get("kind"), rb.get("kind") or "回滚")
            lines.append(f"已排除（回滚到轮 {rb.get('target_round')}，{label}）："
                         + (_clip(rb.get("reasoning"), 120) or "（未记录）"))

        text = "\n".join(lines)
        if max_chars and len(text) > max_chars:
            suffix = f"\n…（摘要截断，原长 {len(text)} 字；完整历史调用 get_work_log）"
            # 提示语也要算进上限内，否则 max_chars 名存实亡、摘要反而撑大窗口
            text = text[:max(0, max_chars - len(suffix))] + suffix
        return text

    def stats(self):
        """给指标用的原始计数（不含解析逻辑）。"""
        events = self.events()
        rounds = [ev.get("round") or 0 for ev in events if ev["t"] == "patch"]
        return {
            "rounds": max(rounds) if rounds else 0,
            "patches": self._count(events, "patch"),
            "tests": self._count(events, "test"),
            "files": len({item.get("path") for ev in events if ev["t"] == "patch"
                          for item in ev.get("files") or []}),
            "rollbacks": self._count(events, "rollback"),
            "probe_rollbacks": self._count_rollbacks(events, "probe"),
        }

    # ---------------- 内部 ----------------

    def _put(self, content):
        """内容寻址：文件名只由内容决定，同样的内容只存一份，与路径无关。"""
        digest = hashlib.sha1(content.encode("utf-8")).hexdigest()
        path = os.path.join(self.blob_dir, digest)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        return digest

    def _get(self, digest):
        with open(os.path.join(self.blob_dir, digest), "r", encoding="utf-8", newline="") as f:
            return f.read()

    def _restore(self, applied):
        restored, broken = [], []
        for path, before in reversed(applied):
            try:
                if before is None:
                    self.io.remove(path)
                else:
                    self.io.write(path, before)
                restored.append(path)
            except Exception as err:
                broken.append(path)
                self._warn(f"还原 {path} 失败: {err}")
        return restored, broken

    def _append(self, event):
        os.makedirs(self.home, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    @staticmethod
    def _count(events, kind):
        return sum(1 for ev in events if ev["t"] == kind)

    @staticmethod
    def _count_rollbacks(events, rollback_kind):
        """回滚事件里指定原因的有几条（探针轮 vs 方向失败，指标要分开看）。"""
        return sum(1 for ev in events
                   if ev["t"] == "rollback" and ev.get("kind") == rollback_kind)

    @staticmethod
    def _warn(message):
        print(f"[worklog] {message}", file=sys.stderr)
