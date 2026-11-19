"""上下文裁剪自检：验证压缩时工具输出与补丁参数都被对称截断。

    python tools/selftests/context_selftest.py

覆盖（方向 B + L2 价值裁剪）：
  - trim_tool_outputs 截断超长的 role="tool" content（留头留尾、标原长）
  - trim_tool_outputs 截断超长的 assistant tool_calls.arguments，且结果仍是合法 JSON
  - 未超限的消息原样保留（不误伤）
  - context_chars 把 tool 参数计入体积，且截断后体积确实下降
  - keep_valuable_turns 保留早期决策轮 + 最近轮，丢弃中间闲聊轮，且无孤立 tool 消息
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
from zerox_core.context import (  # noqa: E402
    keep_valuable_turns, trim_tool_outputs, context_chars, split_turns,
)


def tool_msg(content):
    return {"role": "tool", "tool_call_id": "c1", "name": "run_test", "content": content}


def assistant_msg(arguments):
    return {
        "role": "assistant",
        "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "patch_and_log", "arguments": arguments},
        }],
    }


def main():
    LIMIT = 200

    # 1. 超长 tool 输出被截断，且保留尾部信息 + 原长标注
    big_out = "HEAD" + ("x" * 5000) + "TAIL_MARKER"
    trimmed = trim_tool_outputs([tool_msg(big_out)], LIMIT)
    assert len(trimmed) == 1 and trimmed[0]["role"] == "tool"
    body = trimmed[0]["content"]
    assert "截断，原长" in body and len(body) < LIMIT + 80, (len(body), body[:60])
    assert body.endswith("TAIL_MARKER"), "尾部信息应保留（关键报错在末尾）"

    # 2. 超长补丁参数被截断，且仍是合法 JSON（半截 JSON 会让 API 400）
    big_patch = json.dumps({"changes": [{"path": "Foo.java", "diff": "+" * 5000}]})
    trimmed = trim_tool_outputs([assistant_msg(big_patch)], LIMIT)
    args = trimmed[0]["tool_calls"][0]["function"]["arguments"]
    parsed = json.loads(args)  # 必须能解析
    assert parsed["_truncated"] is True and parsed["_original_chars"] == len(big_patch)
    assert len(args) < LIMIT + 160, len(args)

    # 3. 未超限的消息原样保留
    small = "short output"
    assert trim_tool_outputs([tool_msg(small)], LIMIT)[0]["content"] == small
    small_args = json.dumps({"changes": []})
    assert trim_tool_outputs([assistant_msg(small_args)], LIMIT)[0]["tool_calls"][0]["function"]["arguments"] == small_args

    # 4. context_chars 计入 tool 参数，且截断后体积下降
    before = context_chars([assistant_msg(big_patch)])
    after = context_chars(trim_tool_outputs([assistant_msg(big_patch)], LIMIT))
    assert before > 1000 and after < before, (before, after)

    # 5. L2 价值裁剪：早期决策轮要留住，中间纯闲聊轮要丢，且无孤立 tool 消息
    def turn(tool_name, marker):
        return (
            {"role": "assistant", "tool_calls": [{
                "id": marker, "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps({"k": marker})},
            }]},
            {"role": "tool", "tool_call_id": marker, "name": tool_name, "content": "result-" + marker},
        )

    # 8 轮：round1 是决策(patch)、round2-7 是闲聊(search/read)、round8 是最近决策(test)
    seq = [turn("patch_and_log", "r1")]
    seq += [turn("search_code", f"s{i}") for i in range(2, 8)]
    seq += [turn("run_test", "r8")]
    msgs = [{"role": "system", "content": "main"}, {"role": "system", "content": "state"}]
    for a, t in seq:
        msgs += [a, t]

    kept = keep_valuable_turns(msgs, recent=2, decisions=4)
    content = json.dumps(kept, ensure_ascii=False)
    assert '"r1"' in content and "result-r1" in content, "早期 patch 决策轮应被保留"
    assert '"r8"' in content, "最近 test 决策轮应被保留"
    assert "result-s3" not in content, "中间的纯搜索闲聊轮应被丢弃"
    # 结构合法：每个 assistant 紧跟着自己的 tool，绝不出现孤立 tool 消息
    for i, m in enumerate(kept):
        if m["role"] == "tool":
            assert i > 0 and kept[i - 1]["role"] == "assistant", "出现孤立 tool 消息"

    # split_turns 的 prefix 不含状态卡 / 主 system（它们由调用方显式保留）
    prefix, turns = split_turns(msgs)
    assert len(prefix) == 2 and all(p["role"] == "system" for p in prefix), prefix
    assert len(turns) == 8, len(turns)

    print("context selftest: OK")


if __name__ == "__main__":
    main()
