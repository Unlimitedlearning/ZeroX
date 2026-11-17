"""批量开跑前的一次性准备：把上一轮的 logs/ 整体归档，然后从干净状态开始。

为什么是「归档」而不是「删除」：
  * logs/benchmark_metrics.jsonl 和 logs/audit.jsonl 是唯一的实验记录，删了就没法对账；
  * 归档后 `python src/audit.py --logs logs/archive/<时间戳>` 还能重审上一批（只读 work_log，不跑测试）；
  * 真要彻底删，用 --delete（会二次确认）。

做什么：
  1. 把 logs/ 下除 archive/ 之外的一切（benchmark_metrics.jsonl / audit.jsonl /
     mcp_server.log / 每个 bug 的 work_log + blobs + test.log + verify.log）移动到
     logs/archive/<YYYYmmdd-HHMMSS>/，并写一份 manifest.json；
  2. 顺手清掉 __pycache__（下次运行会自动重建，不影响任何结果）；
  3. 打印 config.json 里这次要跑的 bug 列表和接下来的两条命令。

因为整轮数据都被移走了，不需要再单独备份 metrics：新一轮会重新建文件。

不做的事（故意的）：
  * 不删工作区 d4j_<project>_<version>：src/zerox.py 对每个 bug 的第一次 run_test
    会传 force_checkout=true，服务器自己会重新 checkout；提前删只是白等 2-3 分钟。

用法：
  python tools/new_run.py               # 归档 + 清空，然后开跑
  python tools/new_run.py --dry-run     # 只看会移动什么，不动手
  python tools/new_run.py --delete      # 不归档，直接删（要再输一次 yes）
  python tools/new_run.py --yes         # 跳过确认，适合放进脚本里
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
ARCHIVE_ROOT = os.path.join(LOGS_DIR, "archive")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


def human_size(num):
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024.0


def path_size(path):
    """目录也算总大小（archives 里 blobs 可能不小，先让人看到量级）。"""
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def collect_items():
    """logs/ 下要处理的条目：archive/ 自己留着，其余全算上一轮的数据。"""
    if not os.path.isdir(LOGS_DIR):
        return []
    items = []
    for name in sorted(os.listdir(LOGS_DIR)):
        if name == "archive":
            continue
        items.append(os.path.join(LOGS_DIR, name))
    return items


def clear_pycache():
    removed = []
    for root, dirs, _files in os.walk(BASE_DIR):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in list(dirs):
            if name == "__pycache__":
                target = os.path.join(root, name)
                try:
                    shutil.rmtree(target)
                    removed.append(os.path.relpath(target, BASE_DIR))
                except OSError as err:
                    print(f"  ⚠️ 清不掉 {target}: {err}")
                dirs.remove(name)
    return removed


def configured_bugs():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("benchmark_bugs") or []
    except Exception as err:
        print(f"  ⚠️ 读不到 {CONFIG_FILE}: {err}")
        return []


def main(argv=None):
    parser = argparse.ArgumentParser(description="ZeroX 批量开跑前的数据归档/清空")
    parser.add_argument("--dry-run", action="store_true", help="只列出会移动什么，不动手")
    parser.add_argument("--delete", action="store_true", help="不归档，直接删除上一轮日志")
    parser.add_argument("--yes", action="store_true", help="跳过确认（脚本化用）")
    parser.add_argument("--keep-pycache", action="store_true", help="顺手清 __pycache__ 的行为关掉")
    args = parser.parse_args(argv)

    items = collect_items()
    if not items:
        print("logs/ 已经是干净的，什么都不用做。")
        return 0

    total = sum(path_size(p) for p in items)
    print(f"上一轮数据：{len(items)} 项，共 {human_size(total)}")
    for path in items:
        print(f"  - {os.path.relpath(path, BASE_DIR)}  ({human_size(path_size(path))})")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_dir = os.path.join(ARCHIVE_ROOT, stamp)

    if args.dry_run:
        print(f"\n[dry-run] 以上内容会移动到 {os.path.relpath(archive_dir, BASE_DIR)}")
        return 0

    if args.delete:
        if not args.yes:
            print(f"\n⚠️ --delete 会永久删除上面 {len(items)} 项（含 metrics / audit 记录）。")
            if input("确认删除请输入 yes：").strip().lower() != "yes":
                print("已取消。")
                return 1
        for path in items:
            try:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            except OSError as err:
                print(f"  ⚠️ 删不掉 {path}: {err}（有进程在用？先停掉 src/zerox.py / 残留 java）")
        print(f"\n已删除 {len(items)} 项。")
    else:
        if not args.yes:
            print(f"\n将移动到 {os.path.relpath(archive_dir, BASE_DIR)}")
            if input("确认归档请输入 yes：").strip().lower() != "yes":
                print("已取消。")
                return 1
        os.makedirs(archive_dir, exist_ok=True)
        moved = []
        for path in items:
            dest = os.path.join(archive_dir, os.path.basename(path))
            try:
                shutil.move(path, dest)
                moved.append({"name": os.path.basename(path), "bytes": path_size(dest)})
            except OSError as err:
                print(f"  ⚠️ 移动失败 {path}: {err}（有进程在用？先停掉 src/zerox.py / 残留 java）")
        manifest = {
            "archived_at": stamp,
            "items": moved,
            "benchmark_bugs": configured_bugs(),
        }
        with open(os.path.join(archive_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print(f"\n已归档 {len(moved)} 项 → {os.path.relpath(archive_dir, BASE_DIR)}")
        print(f"以后要重审这一批：python src/audit.py --logs {os.path.relpath(archive_dir, BASE_DIR)}")

    if not args.keep_pycache:
        removed = clear_pycache()
        if removed:
            print(f"清理 __pycache__：{', '.join(removed)}")

    bugs = configured_bugs()
    if bugs:
        print(f"\n这一轮 config.json 的 bug 列表（{len(bugs)} 个）：{' '.join(bugs)}")
        print("logs/ 现在是空的，src/zerox.py 会重新建 benchmark_metrics.jsonl。")
    else:
        print("\n⚠️ config.json 里没有 benchmark_bugs，先配上再跑。")
    print("\n接下来：\n  python src/zerox.py\n  python src/audit.py            # 汇总 clean / invalid\n  python src/audit.py --verify   # 每个 Fixed 多 3-6 分钟，换 Verified Fixed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
