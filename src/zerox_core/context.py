"""上下文裁剪的纯函数（零运行期状态，可离线单测）。

从 src/zerox.py 抽出来，是因为这些函数不依赖客户端 / 服务端 / 配置，却被压缩主循环
直接调用：放进 zerox_core 后，selftest 不必 import 入口脚本（import 入口会初始化
AsyncOpenAI client、读取 config.json，带来副作用）。

    trim_tool_outputs   对称截断工具输出与补丁参数（防大 diff 二次撑爆窗口）
    keep_valuable_turns 按价值裁剪：保最近轮 + 最近决策轮，丢纯搜索闲聊轮
    context_chars       统计上下文体积，作为压缩触发的代理值

依赖方向：被 src/zerox.py 依赖；本模块只依赖标准库 json。
"""

import json


def _call_function(call):
    """从 dict 或 SDK 对象形态的 tool_call 里取出 function（取不到返回 None）。"""
    return call.get("function") if isinstance(call, dict) else getattr(call, "function", None)


def _call_arguments(call):
    """取 tool_call.function.arguments 字符串；取不到返回空串。"""
    fn = _call_function(call)
    if fn is None:
        return ""
    value = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", "")
    return value if isinstance(value, str) else ""


def _clip_arguments(args, limit):
    """把超长的 tool_calls.arguments 压成合法 JSON 占位串（留头留尾 + 原长）。

    用 json.dumps 而不是裸截断，是为了让结果仍是合法 JSON —— 历史 assistant 消息会被
    原样回传 API，半截 JSON 参数在部分 provider 上会触发 400。完整参数能从 get_work_log
    找回，所以这里只保留供模型辨认方向的头尾片段。
    """
    if not isinstance(args, str) or len(args) <= limit:
        return args
    head_len = limit * 2 // 3
    head = args[:head_len]
    tail = args[-(limit - head_len - 160):] if limit - head_len > 160 else ""
    return json.dumps({
        "_truncated": True,
        "_original_chars": len(args),
        "head": head,
        "tail": tail,
        "_note": "参数过长已裁剪；完整内容见 get_work_log",
    }, ensure_ascii=False)


def trim_tool_outputs(msgs, limit):
    """把保留轮次里过大的工具输出 / 补丁参数掐到约 limit 字（留头留尾，中间标原长）。

    - role="tool"：content 超长时截断（dict，安全复制）。
    - role="assistant"：tool_calls[].function.arguments 超长时截断。
      以前只截 tool 消息，assistant 里的大补丁 diff 原样留下：context_chars 把它算进
      体积，这里却不截 —— 两边不对称，压缩后一条大补丁参数就能再次撑爆窗口。

    只在确实超长时才把 SDK 对象 model_dump 成 dict（dict 可安全回传 API，且不原地改对象），
    并从结果里剔除 reasoning 字段，避免 reasoner 的推理内容被回传（会 400）。
    """
    out = []
    for msg in msgs:
        is_dict = isinstance(msg, dict)
        role = msg.get("role") if is_dict else getattr(msg, "role", "")

        if is_dict and role == "tool":
            content = msg.get("content")
            if isinstance(content, str) and len(content) > limit:
                head = content[: limit * 2 // 3]
                msg = dict(msg, content=(
                    f"{head}\n…（截断，原长 {len(content)} 字）…\n"
                    f"{content[len(head):][-(limit - len(head)):]}"
                ))
            out.append(msg)
            continue

        if role != "assistant":
            out.append(msg)
            continue

        calls = msg.get("tool_calls") if is_dict else getattr(msg, "tool_calls", None)
        if not any(len(_call_arguments(call)) > limit for call in calls or []):
            out.append(msg)
            continue

        # 有超长参数：转成纯 dict 后统一裁剪，避免混用 dict / SDK 对象
        if is_dict:
            base = dict(msg)
        else:
            base = msg.model_dump() if hasattr(msg, "model_dump") else dict(vars(msg))
        for key in ("reasoning_content", "reasoning"):  # reasoner 的推理内容禁止回传
            base.pop(key, None)
        new_calls = []
        for call in base.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn, dict):
                new_calls.append(call)
                continue
            args = fn.get("arguments") or ""
            clipped = _clip_arguments(args, limit)
            new_calls.append(dict(call, function=dict(fn, arguments=clipped)) if clipped != args else call)
        base["tool_calls"] = new_calls
        out.append(base)
    return out


def context_chars(messages):
    """当前上下文里所有消息正文 + 工具参数的字符数（不是 token，也不是日志文件大小）。

    为什么不用 transcript.md 的大小当触发条件：那份文件只增不删，压缩之后它不会变小，
    一次压缩后就再也不会触发第二次，除非额外记一个「上次压缩时的 offset」。
    直接数 messages 没有这个问题，也不依赖磁盘。
    """
    total = 0
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        if isinstance(content, str):
            total += len(content)
        elif content is not None:
            total += len(str(content))
        # tool_calls 的参数（patch 的 diff 之类）同样占上下文，不能漏
        calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
        for call in calls or []:
            total += len(_call_arguments(call))
    return total


# 决策轮：这些调用改变了工作区或提供了真值，它们的 assistant+tool 对在压缩时要优先保留。
# 纯搜索 / 读代码（search_code / read_specific_code / get_file_internal_structure）属于可丢弃的闲聊轮。
DECISION_TOOLS = {"patch_and_log", "run_test", "rollback"}


def _msg_role(msg):
    return msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", "")


def split_turns(messages):
    """把消息切成若干「轮」：每轮以一条 assistant 开头，后跟它触发的 0..N 条 tool 结果。

    assistant 之前的 system / user 前缀不属于任何轮，原样作为 prefix 返回。
    返回 (prefix, [(assistant, [tool, ...]), ...])。切点始终落在 assistant 上，
    因此任何一轮的切片都不会出现「孤立的 tool 消息」（那会直接 400）。
    """
    prefix, turns, current = [], [], None
    for msg in messages:
        if _msg_role(msg) == "assistant":
            current = (msg, [])
            turns.append(current)
        elif current is None:
            prefix.append(msg)
        else:
            current[1].append(msg)
    return prefix, turns


def turn_is_decision(turn):
    """一轮里只要调用过决策工具，就认定为高价值轮。"""
    assistant, _ = turn
    calls = assistant.get("tool_calls") if isinstance(assistant, dict) else getattr(assistant, "tool_calls", None)
    for call in calls or []:
        fn = _call_function(call)
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "")
        if name in DECISION_TOOLS:
            return True
    return False


def keep_valuable_turns(messages, recent=2, decisions=4):
    """按价值而非时间挑选保留轮：最近的 recent 轮 + 最近的 decisions 个决策轮。

    动机：early 想通的关键假设（写在 patch_and_log.reasoning 里）可能发生在十几轮之前，
    「只留最近 2 轮」会把它连同推理链一起丢掉；而夹在中间的纯搜索轮没有保留价值。
    被选中的轮按原始顺序重排、保持 [assistant + 它的 tools] 完整，结果可安全回传 API。

    prefix（system / user 前缀）由调用方单独处理，不在返回里。
    """
    _, turns = split_turns(messages)
    if not turns:
        return []
    chosen = set(range(max(0, len(turns) - recent), len(turns)))  # 最近 recent 轮
    decision_idx = [i for i, t in enumerate(turns) if turn_is_decision(t)]
    chosen.update(decision_idx[-decisions:])  # 最近 decisions 个决策轮（可能与上面重叠）
    out = []
    for i in sorted(chosen):
        assistant, tools = turns[i]
        out.append(assistant)
        out.extend(tools)
    return out
