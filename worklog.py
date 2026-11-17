"""worklog —— 给 agent 用的最小版本控制系统（与 APR 无关，可单独复用）。

三个概念：
  blob    文件内容按 sha1 存一份，和路径无关，天然去重，也不会被同名文件撞掉
  event   一条 JSON 事件（patch / test / note），append-only
  round   一轮修复；rollback(to_round) 撤销该轮及之后的全部 patch

为什么长这样（每条都是对旧实现的直接修正）：
  1. 目标文件的读写通过 io 回调注入。旧实现把 `write_target_file`（Windows→WSL 的
     「临时文件 + cp」）写死在备份逻辑旁边，导致这套东西只能在 ZeroX 里用；
     注入之后，本地文件、WSL 工作区、远端容器都能跑同一份代码。
  2. 事件只能追加。旧实现每次跑完测试就把**所有历史** test_result 清掉只留最新，
     等于系统主动删掉了 agent 最需要的那条信号：失败数是在收敛还是在发散。
  3. patch 事件自带 before/after 的 blob 引用。旧实现靠「路径扁平化成 .bak 文件名」
     恢复，既会撞名，也表达不了「新建文件」；现在 before=null 就是新建，回滚即删除。

日志格式（work_log.jsonl，一行一条，永不改成「覆盖写」）：
  {"t":"patch","ts":"2026-11-17T14:12:05","round":3,"status":"PATCH_GENERATED",
   "reasoning":"...","summary":"...",
   "files":[{"path":"/ws/src/Foo.java","before":"<sha1>"|null,"after":"<sha1>",
             "added":4,"removed":2}]}
  {"t":"test","ts":"...","round":3,"status":"Failed","fail_count":5,"detail":"..."}
  {"t":"note","ts":"...","text":"..."}
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


def _normalize(record):
    """把一条日志记录统一成事件列表；兼容没有 t 字段的旧格式（一轮 = 一条记录）。"""
    if not isinstance(record, dict):
        return [{"t": "note", "ts": "", "text": str(record)}]

    if record.get("t") in ("patch", "test", "note"):
        return [record]

    files = []
    for change in record.get("changes") or []:
        if isinstance(change, dict) and change.get("file_path"):
            # 老格式没有 blob 引用：能渲染，但不能用来自动回滚（rollback 会跳过）
            files.append({"path": change["file_path"], "before": None, "after": None,
                          "added": 0, "removed": 0})

    events = [{
        "t": "patch",
        "ts": record.get("timestamp", ""),
        "round": record.get("round"),
        "status": record.get("status"),
        "reasoning": record.get("reasoning"),
        "summary": record.get("fix_summary"),
        "files": files,
    }]
    old_test = record.get("test_result")
    if isinstance(old_test, dict):
        events.append({
            "t": "test",
            "ts": old_test.get("timestamp", ""),
            "round": record.get("round"),
            "status": old_test.get("test_status"),
            "fail_count": old_test.get("fail_count"),
            "detail": old_test.get("detail", ""),
        })
    return events


def _stamp(ts):
    """ISO 时间 → HH:MM:SS，digest 里只需要这个精度。"""
    return ts[11:19] if isinstance(ts, str) and len(ts) >= 19 else (ts or "")


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


class WorkLog:
    """一个目录 = 一个版本库：home 下同时放 log、audit 和 blobs。"""

    def __init__(self, home, io=None, test_tail=2000):
        self.home = home
        self.blob_dir = os.path.join(home, "blobs")
        self.log_path = os.path.join(home, "work_log.jsonl")
        self.audit_path = os.path.join(home, "human_audit.jsonl")
        self.io = io or LocalIO()
        self.test_tail = test_tail

    # ---------------- 写 ----------------

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

    def note(self, text):
        """追加一条自由笔记，给 agent 记「为什么推翻上一轮」这类没法写进 patch 的结论。"""
        event = {"t": "note", "ts": _now(), "text": text}
        self._append(event)
        return event

    def rollback(self, to_round):
        """撤销 >= to_round 的全部 patch，并把被撤销的事件归档到 human_audit.jsonl。

        返回 (已恢复文件列表, 还原失败文件列表)。一个文件都没恢复就完全不动日志，
        避免把一次失败的回滚变成一次静默的数据丢失。
        """
        events = self.events()
        dropped = [ev for ev in events if ev["t"] == "patch" and _round_reached(ev.get("round"), to_round)]

        restored, broken = [], []
        # 倒序撤销：同一个文件被改过多次时，最后落地的 before 才是原始内容
        for event in reversed(dropped):
            for item in reversed(event.get("files") or []):
                path = item.get("path")
                if not path:
                    continue
                if item.get("after") is None:
                    continue  # 老格式（没有 blob）：只能渲染，不能回滚
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

        return restored, broken

    # ---------------- 读 ----------------

    def events(self):
        """按写入顺序读出全部事件；坏行跳过，老格式自动升级。"""
        if not os.path.exists(self.log_path):
            return []
        result = []
        with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                result.extend(_normalize(record))
        return result

    def digest(self, limit=5, title=None, detail_chars=1200):
        """给 agent 看的紧凑视图：每轮改了什么、测试怎么变的、还差什么。

        只给摘要，不回放 old/new 全文 —— 那是让模型花钱读自己刚写过的代码。
        """
        events = self.events()
        who = f"当前 Bug（{title}）" if title else ""
        if not events:
            return f"{who}暂无历史工作日志。"

        groups = []
        for event in events:
            if event["t"] == "patch":
                groups.append({"round": event.get("round"), "patch": event, "tests": [], "notes": []})
            elif groups:
                groups[-1]["tests" if event["t"] == "test" else "notes"].append(event)
            else:
                groups.append({"round": event.get("round"), "patch": None,
                               "tests": [event] if event["t"] == "test" else [], "notes": []})

        shown = groups[-limit:] if isinstance(limit, int) and limit > 0 else groups

        # 每个测试事件对应的「上一次失败数」，用于渲染好转/变差
        previous, previous_map = None, {}
        for event in events:
            if event["t"] == "test":
                previous_map[id(event)] = previous
                previous = event.get("fail_count")

        lines = [
            f"{who}最近 {len(shown)} 轮记录"
            f"（共 {len(groups)} 轮 / {self._count(events, 'patch')} 个补丁 / {self._count(events, 'test')} 次测试）"
        ]
        for group in shown:
            patch = group["patch"]
            head_event = patch or (group["tests"] or group["notes"] or [{}])[0]
            head = f"[轮 {group.get('round')}] {_stamp(head_event.get('ts'))}"
            if patch:
                summary = (patch.get("summary") or "").strip()
                lines.append(f"{head} {patch.get('status') or 'PATCH'}  {summary}".rstrip())
                for item in patch.get("files") or []:
                    name = str(item.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
                    delta = ""
                    if item.get("after") is not None:
                        delta = f" +{item.get('added', 0)}/-{item.get('removed', 0)}"
                    lines.append(f"        {name}{delta}")
            else:
                lines.append(head)
            for event in group["tests"]:
                lines.append("        " + _test_line(event, previous_map.get(id(event))))
            for event in group["notes"]:
                lines.append(f"        笔记：{event.get('text')}")

        latest = [ev for ev in events if ev["t"] == "test"]
        if latest and detail_chars:
            detail = (latest[-1].get("detail") or "").strip()
            if detail:
                lines.append("")
                lines.append(f"--- 最近一次测试详情（尾 {detail_chars} 字）---")
                lines.append(detail[-detail_chars:])
        return "\n".join(lines)

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
        }

    # ---------------- 内部 ----------------

    def _put(self, content):
        """内容寻址：同样的内容只存一份，路径不再参与命名。"""
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
    def _warn(message):
        print(f"[worklog] {message}", file=sys.stderr)
