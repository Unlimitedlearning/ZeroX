"""打印某个 bug 渲染后的 setup_cmd / test_cmd，用来核对配置里的占位符有没有拼错。

用法：
    python tools/show_setup_cmd.py Chart_1
    python tools/show_setup_cmd.py case/config.json List_999
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from zerox_core import config as ZC  # noqa: E402

args = sys.argv[1:]
if not args:
    print(__doc__)
    sys.exit(1)

if len(args) >= 2 and args[0].endswith(".json"):
    cfg_path = args[0] if os.path.isabs(args[0]) else os.path.join(ROOT, args[0])
    bug_id = args[1]
else:
    cfg_path = os.path.join(ROOT, "config.json")
    bug_id = args[0]

config = ZC.load_config(cfg_path)
target = ZC.load_target_config(config, cfg_path)
ctx = ZC.resolve_bug(target, bug_id)
if ctx is None:
    print(f"bug_id 不符合 target.bug_id_pattern：{bug_id}")
    sys.exit(1)

print(f"bug_id   : {ctx['bug_id']}")
print(f"workspace: {ctx['workspace']}")
print(f"setup_cmd: {ctx['setup_cmd']}")
print(f"test_cmd : {ctx['test_cmd']}")
