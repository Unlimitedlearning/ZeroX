"""ZeroX 事后审计：任务跑完之后才判定，agent 运行时不受任何编辑限制。

为什么放在循环外面（和 worklog.py 同一条原则：判定逻辑要能被别的 agent 复用）：
  * 运行时拦「动测试代码」有两个副作用：合法 patch 被反复拒绝（模型常给相对路径），
    被拒绝的 agent 转头随便打个补丁换权限，一次就是 3-6 分钟的测试。
  * 判定需要的信息本来就全在 work_log 里：每轮 patch 改了哪些文件、测试怎么变的。
    所以同一个审计器可以离线重放历史 run，不用把测试再跑一遍。

两个标签，分开报：
  audit    clean | invalid   —— invalid = 某一轮 patch 动过测试代码
  verified true | false      —— 把测试文件还原成原始状态后重跑仍然通过（只有 --verify 才算）

用法（代码在 src/，日志和配置在项目根目录）：
  python src/audit.py                  # 审计 logs/ 下所有 bug（只读 work_log，不跑测试）
  python src/audit.py --bug Lang_1     # 只审计一个（前缀匹配 Lang_1 -> Lang_1b）
  python src/audit.py --verify         # 额外把测试文件还原后重跑，每个 bug 3-6 分钟

审计结论追加到 logs/audit.jsonl，一行一个 bug，可以和历史 run 对账。
"""
import argparse
import fnmatch
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime

# 审计规则一变就 +1；版本号同时写进 metrics 行，否则同一份日志里的结论分不清按哪版规则得出
AUDIT_VERSION = 1

AUDIT_LOG_NAME = "audit.jsonl"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def normalize_path(path) -> str:
    """统一成 posix 风格，方便跨平台比对（审计可能跑在 Windows 上）。"""
    return str(path or "").strip().replace("\\", "/")


def match_any_glob(path: str, globs) -> bool:
    """逐个 fnmatch。

    必须用 fnmatchcase 而不是 fnmatch：Windows 上 fnmatch 会先 normcase 转小写，
    于是 D:\\...\\Test\\ 里的 "Test" 会命中 */test/*，把整个工作区误判成测试目录。
    """
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def load_events(log_path):
    """读 work_log.jsonl；坏行跳过，不让一条脏数据废掉整次审计。"""
    events = []
    if not log_path or not os.path.exists(log_path):
        return events
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def audit_events(events, test_globs=None):
    """纯函数：给定 work_log 事件流，给出审计结论。

    只认两件事：
      * patch 事件里出现过的每一个文件路径（是否属于测试代码）
      * test 事件的数量与最后一条的状态（用于报告复现成本）
    不给 events 也能跑（返回 clean + 0 补丁），方便历史 run 缺日志时对账。
    空列表（config 里写 `"audit_test_globs": []`）表示「本项目分不出测试代码」，
    默认值只有一份，在 zerox_core/config.py 的 DEFAULT_TARGET 里：两份默认值迟早会不一致，
    而审计标准不一致比没有标准更糟。
    """
    globs = [str(g).strip() for g in (test_globs or []) if str(g).strip()]

    patches = []
    test_events = []
    touched = {}

    for event in events:
        kind = event.get("t")
        if kind == "patch":
            patches.append(event)
            round_no = event.get("round")
            for item in event.get("files") or []:
                path = normalize_path(item.get("path"))
                if not path:
                    continue
                record = touched.setdefault(path, {"path": path, "rounds": [], "created": False})
                if round_no is not None and round_no not in record["rounds"]:
                    record["rounds"].append(round_no)
                # before 为空 = 这一轮新建的文件，还原时只能删掉，git checkout 救不回来
                if item.get("before") is None:
                    record["created"] = True
        elif kind == "test":
            test_events.append(event)

    files = sorted(touched.values(), key=lambda r: r["path"])
    violations = [record for record in files if match_any_glob(record["path"], globs)]
    last_test = test_events[-1] if test_events else {}

    return {
        "audit": "invalid" if violations else "clean",
        "violations": violations,
        "files": files,
        "patches": len(patches),
        "tests": len(test_events),
        "rounds": max([ev.get("round") or 0 for ev in patches] or [0]),
        "last_test_status": last_test.get("status"),
        "last_fail_count": last_test.get("fail_count"),
        "events": len(events),
    }


def find_bug_dir(logs_dir, bug_id):
    """按 bug 名找日志目录。

    bug_id 是「请求名」（Lang_1），日志目录是「规范名」（Lang_1b），所以先精确匹配，
    再接受只差 b/f 后缀的前缀匹配；命中多个就报错，绝不猜。
    """
    logs_dir = str(logs_dir)
    exact = os.path.join(logs_dir, bug_id)
    if os.path.exists(os.path.join(exact, "work_log.jsonl")):
        return exact

    try:
        names = os.listdir(logs_dir)
    except OSError:
        return None

    have_log = [
        n for n in names
        if os.path.isdir(os.path.join(logs_dir, n))
        and os.path.exists(os.path.join(logs_dir, n, "work_log.jsonl"))
    ]
    near = [n for n in have_log if n.startswith(bug_id) and n[len(bug_id):] in ("", "b", "f")]
    if len(near) == 1:
        return os.path.join(logs_dir, near[0])
    if len(near) > 1:
        raise ValueError(f"{bug_id} 匹配到多个日志目录：{sorted(near)}，请写全名")

    loose = [n for n in have_log if n.startswith(bug_id)]
    if len(loose) == 1:
        return os.path.join(logs_dir, loose[0])
    if len(loose) > 1:
        raise ValueError(f"{bug_id} 匹配到多个日志目录：{sorted(loose)}，请写全名")
    return None


def audit_bug_dir(bug_dir, test_globs=None):
    """审计一个 bug 的日志目录（只读 work_log，不跑测试，几毫秒）。"""
    log_path = os.path.join(str(bug_dir), "work_log.jsonl")
    verdict = audit_events(load_events(log_path), test_globs=test_globs)
    verdict["bug_id"] = os.path.basename(str(bug_dir).rstrip("/\\"))
    verdict["bug_dir"] = str(bug_dir)
    verdict["audit_version"] = AUDIT_VERSION
    return verdict


def audit_bug(logs_dir, bug_id, test_globs=None):
    """按 bug 名审计；找不到日志返回 audit=unknown（不是 clean，别把没跑过的算成干净）。"""
    try:
        bug_dir = find_bug_dir(logs_dir, bug_id)
    except ValueError as err:
        return {"bug_id": bug_id, "audit": "unknown", "reason": str(err), "violations": []}
    if not bug_dir:
        return {"bug_id": bug_id, "audit": "unknown", "reason": "没有 work_log.jsonl", "violations": []}
    return audit_bug_dir(bug_dir, test_globs=test_globs)


def _decode(output):
    if isinstance(output, bytes):
        return output.decode("utf-8", "replace")
    return output or ""


def verify_fixed(bug_dir, workspace, test_cmd, passed_pattern,
                 verdict=None, timeout=None):
    """把测试文件还原成原始状态后重跑一遍：还过 -> verified。

    只有 audit 结论是 invalid（动过测试）时才需要还原；audit 干净也照样复跑一次，
    用来挡掉 flaky pass —— 「一次通过」不等于「修好了」。
    还原规则：work_log 里 before 为空的是新建文件（rm -f），其余走 git checkout --。
    """
    from zerox_core.exec_env import execute_in_target, popen_in_target  # 只有验证才需要起进程

    if not str(passed_pattern or "").strip():
        return {"verified": False, "reason": "config 没有 passed_pattern，无法判定",
                "reverted": [], "notes": []}

    reverted, notes = [], []
    violations = (verdict or {}).get("violations") or []
    if violations:
        created = sorted(v["path"] for v in violations if v.get("created"))
        modified = sorted(v["path"] for v in violations if not v.get("created"))
        steps = []
        if modified:
            steps.append("git checkout -- " + " ".join(shlex.quote(p) for p in modified))
        if created:
            steps.append("rm -f " + " ".join(shlex.quote(p) for p in created))
        restore_cmd = f"cd {shlex.quote(workspace)} && " + " && ".join(steps)
        res = execute_in_target(restore_cmd)
        reverted = modified + created
        if res.returncode != 0:
            notes.append(f"还原失败（退出码 {res.returncode}）：{_decode(res.stderr)[-400:]}")
            return {"verified": False, "reason": "测试文件还原失败", "reverted": reverted,
                    "notes": notes}

    # 输出直接写盘而不是走 PIPE：复跑要 3-6 分钟，走 PIPE 意味着这几分钟里一个字也看不到，
    # 看上去就是「卡住」。写文件后旁边 tail -f logs/<bug>/verify.log 就能看进度。
    log_path = os.path.join(str(bug_dir), "verify.log") if bug_dir else None
    handle = None
    try:
        if log_path:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handle = open(log_path or os.devnull, "w", encoding="utf-8")

        print(f"[verify] 开始复跑（超时 {timeout}s）：{test_cmd}", flush=True)
        if log_path:
            print(f"[verify] 实时输出：{log_path}（另开一个终端 tail -f 看进度）", flush=True)

        try:
            proc = popen_in_target(test_cmd, stdout=handle, stderr=subprocess.STDOUT)
        except Exception as err:
            return {"verified": False, "reason": f"启动测试失败: {err}", "reverted": reverted,
                    "notes": notes}

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if handle is not None:
            handle.close()

    print(f"[verify] 复跑结束（退出码 {proc.returncode}，超时={timed_out}）", flush=True)

    text = ""
    if log_path and os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()

    if timed_out:
        return {"verified": False, "reason": f"复跑超时（>{timeout}s）", "reverted": reverted,
                "notes": notes}

    try:
        passed = re.search(passed_pattern, text) is not None
    except re.error as err:
        return {"verified": False, "reason": f"passed_pattern 不是合法正则: {err}",
                "reverted": reverted, "notes": notes}
    reason = "还原测试文件后仍然通过" if passed and reverted else (
        "原样复跑通过" if passed else "复跑未通过")
    return {
        "verified": passed,
        "reason": reason,
        "reverted": reverted,
        "notes": notes,
        "tail": text[-1200:],
    }


def _write_row(logs_dir, row):
    try:
        os.makedirs(logs_dir, exist_ok=True)
        with open(os.path.join(logs_dir, AUDIT_LOG_NAME), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as err:
        print(f"⚠️ 写入 {AUDIT_LOG_NAME} 失败: {err}")


def _print_verdict(row):
    mark = {"clean": "✅", "invalid": "⚠️", "unknown": "❔"}.get(row.get("audit"), "•")
    detail = ""
    if row.get("audit") == "invalid":
        parts = []
        for item in row.get("violations") or []:
            path = item.get("path") if isinstance(item, dict) else str(item)
            rounds = item.get("rounds") if isinstance(item, dict) else None
            name = os.path.basename(str(path))
            parts.append(f"{name}@轮{rounds}" if rounds else name)
        detail = " 动过测试：" + ", ".join(parts)
    elif row.get("audit") == "unknown":
        detail = f" （{row.get('reason')}）"
    verified = row.get("verified")
    verify_text = "" if verified is None else (" · Verified" if verified else " · 未复现")
    print(f"{mark} {row['bug_id']:<12} {row.get('audit', '?'):<8} "
          f"补丁 {row.get('patches', 0):<3} 测试 {row.get('tests', 0):<3}{verify_text}{detail}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="ZeroX 事后审计（读 work_log，判定 Fixed / Verified Fixed）")
    parser.add_argument("--config", default=None, help="配置文件，默认 ZEROX_CONFIG 或 <项目根>/config.json")
    parser.add_argument("--logs", default=None, help="日志根目录，默认 <项目根>/logs")
    parser.add_argument("--bug", nargs="*", default=None, help="要审计的 bug 名；省略 = logs/ 下全部")
    parser.add_argument("--verify", action="store_true", help="还原测试文件后重跑（每个 bug 3-6 分钟）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 行而不是表格")
    parser.add_argument("--no-write", action="store_true", help=f"不追加 logs/{AUDIT_LOG_NAME}")
    args = parser.parse_args(argv)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    # src/ 只放代码：默认的 logs/ 和 config.json 都在项目根目录
    project_root = os.path.dirname(base_dir)
    logs_dir = args.logs or os.path.join(project_root, "logs")
    config_file = args.config or os.getenv("ZEROX_CONFIG") or os.path.join(project_root, "config.json")

    target = None
    config = None
    if os.path.exists(config_file):
        from zerox_core import config as config_mod
        config = config_mod.load_config(config_file)
        target = config_mod.load_target_config(config, config_file)

    test_globs = (target or {}).get("audit_test_globs")
    if test_globs is None:
        # 配置文件缺失 / 没写这一项：审计就分不出测试代码，只能数补丁和测试次数。
        # 这种情况必须说出来 —— 否则一份「全 clean」的报告会被当成「全部没动测试」。
        print(f"⚠️ 未读到 audit_test_globs（配置文件：{config_file}），本次审计不判定测试文件改动。")
        test_globs = []
    test_timeout = ((config or {}).get("timeouts") or {}).get("test_execution") or 1800

    if args.bug:
        bug_names = list(args.bug)
    else:
        try:
            bug_names = sorted(
                n for n in os.listdir(logs_dir)
                if os.path.exists(os.path.join(logs_dir, n, "work_log.jsonl"))
            )
        except OSError:
            bug_names = []

    if not bug_names:
        print(f"没有找到可审计的 work_log（目录：{logs_dir}）")
        return 1

    print(f"审计 {len(bug_names)} 个 bug（test_globs={test_globs}）")
    rows = []
    for name in bug_names:
        verdict = audit_bug(logs_dir, name, test_globs=test_globs)
        row = {
            "timestamp": _now(),
            "audit_version": AUDIT_VERSION,
            "bug_id": verdict.get("bug_id") or name,
            "audit": verdict.get("audit"),
            "violations": [
                {"path": v["path"], "rounds": v.get("rounds") or [], "created": v.get("created")}
                for v in verdict.get("violations") or []
            ],
            "patches": verdict.get("patches", 0),
            "tests": verdict.get("tests", 0),
            "rounds": verdict.get("rounds", 0),
            "last_test_status": verdict.get("last_test_status"),
            "verified": None,
        }

        if args.verify:
            resolved = config_mod.resolve_bug(target, name) if target else None
            if not resolved:
                row["verified"] = False
                row["verified_reason"] = "无法从 config 解析工作区（bug_id 不匹配 bug_id_pattern）"
            else:
                try:
                    outcome = verify_fixed(
                        verdict.get("bug_dir"),
                        resolved["workspace"],
                        resolved["test_cmd"],
                        (target or {}).get("passed_pattern") or "",
                        verdict=verdict,
                        timeout=test_timeout,
                    )
                except KeyboardInterrupt:
                    print("\n[已中断] 复跑被手动终止；工作区代码未被改动，本轮结论未写入 audit.jsonl。")
                    return 130
                row["verified"] = bool(outcome.get("verified"))
                row["verified_reason"] = outcome.get("reason")
                row["reverted"] = outcome.get("reverted") or []

        rows.append(row)
        if args.json:
            print(json.dumps(row, ensure_ascii=False))
        else:
            _print_verdict(row)

    if not args.no_write:
        for row in rows:
            _write_row(logs_dir, row)

    if not args.json:
        total = len(rows)
        clean = sum(1 for r in rows if r["audit"] == "clean")
        invalid = sum(1 for r in rows if r["audit"] == "invalid")
        unknown = sum(1 for r in rows if r["audit"] == "unknown")
        line = f"合计 {total}：clean {clean} · invalid {invalid} · unknown {unknown}"
        if args.verify:
            verified = sum(1 for r in rows if r.get("verified"))
            line += f" · Verified Fixed {verified}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
