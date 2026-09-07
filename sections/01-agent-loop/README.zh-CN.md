# 1 · Agent Loop

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md)

> 一个 loop 反复调用模型，直到模型给出答案或要求使用工具。

一般的模型调用就是一问一答：送出 messages，收到一次响应。

agent 不只要取得响应，还要执行模型要求的工具，把结果加回对话，再调用一次模型。因此，同一份 `messages[]` 会在整个轮次中持续累积内容。

这个 loop 必须：

1. 在多次模型调用之间保留对话状态。
2. 判断模型要使用工具，还是已经给出最终答案。
3. 执行指定的工具，并把结果加入对话。
4. 重复以上流程，直到模型结束这一轮。

没有这个 loop，模型只能判断该怎么做，却不能真的动手。loop 若设计错误，可能太早停止，也可能永远停不下来。

---

## 核心机制

![机制图](assets/01-agent-loop.png)

这里要分清楚两层 loop，它们共享同一份 `messages[]`。

可以把它想成一个聊天窗口。你问「台北现在天气如何？要不要带伞？」，模型可能先查目前天气，再查降雨概率，最后才整理成答案。
**所以同一个轮次里，模型往往被调用好几次，中间穿插各种工具调用。**
从发问到收到完整答案的整段流程，就是内层 loop，也就是一个用户轮次（turn）。它会调用模型、检查 `stop_reason`、视需要执行工具并加入结果，直到模型给出最终答案。

接着你在同一个窗口再问「那明天呢？」，这就是新的一轮。
负责把一轮接一轮串成完整对话的，则是外层 loop。每一轮都会加到同一份 `messages[]`，所以模型回答明天的天气时，仍然知道你问的是台北。

内层 loop 就是拿着调用方手上那份 `messages[]`，把一轮跑完：

```python
def run_turn(messages, model, max_steps=10):        # src/loop.py · one turn over the shared messages[]
    for _ in range(max_steps):                       # the inner loop, with a backstop
        response = model(messages)                   # one Anthropic Messages call
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":       # model produced its answer for this turn
            return final_text(response)

        results = []                                 # tool_use: run each, feed back
        for block in response.content:
            if block.type == "tool_use":
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": run_tool(block.name, block.input)})
        messages.append({"role": "user", "content": results})

    raise RuntimeError("hit max_steps without end_turn")
```

- [`src/loop.py`](src/loop.py) 中的 `run_turn()` 就是内层 loop。
- `messages` 是采用 Anthropic Messages 格式的共享状态。
- `max_steps` 是防止 loop 失控的安全上限。
- `run_tool(name, input)` 解析出工具、执行它，并返回供 `tool_result` 使用的文字。
- [`src/demo.py`](src/demo.py) 中的 `model()` 是一次 `client.messages.create` 调用。loop 不绑定单一供应商。

外层 loop 每一轮附加一则用户消息，并保留整个缓冲区：

```python
messages = []                                        # src/demo.py · the conversation, owned by the caller
for user_text in turns:                              # the outer loop: one iteration per user turn
    messages.append({"role": "user", "content": user_text})
    reply = run_turn(messages, model)                # appends in place; turn N sees turns 1..N-1
```

有两个 `stop_reason` 值驱动这个 loop：

- `tool_use`：执行工具、附加结果，再次调用模型。
- `end_turn`：返回最终答案。demo 只要遇到任何不是 `tool_use` 的值就停止。

`messages[]` 是这个 session 的整段对话记忆。工具结果与 assistant 回复都会放进去。下一次模型调用会在这整份状态上进行推理。

这个最精简的 loop 没有权限关卡。第 3 章会在工具执行前加上权限。

---

## 不同系统怎么做

各个 agent 如何拥有这个 loop，以及如何决定何时停止。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | 能实时输出进度、把关副作用，还能并行执行工具。 | loop 很小，容易阅读与审计。 | loop 可以整个换掉，每个阶段都能拦截，log 可以重放。 |
| **限制** | loop 包在一个更大的 runtime 里。 | 无法把关副作用，不能实时输出进度，也不能并行执行工具。 | 活动零件最多。得先懂 turn、step、inbox 这套词汇。 |
| **设计原因** | 核心分支保持不变，功能都加在外围。 | 小 loop 本身就是目的。检测任务是否完成的是环境，不是模型。 | loop 就是众多 plugin 里的一个。 |
| **做法：loop driver** | 一个 async generator。每个工具都通过同一份契约接上来。 | 一个 while loop。每一步跟模型要一道指令，再执行。 | 一个可换掉的 plugin，跑在一份 durable 事件 log 上。 |
| **做法：stop signal** | `stop_reason: end_turn`。 | 由环境检测提交标记，附加一则 `role: "exit"` 消息。 | 没有待处理项、检查点没有挡着，或某个 tool result 直接结束这一轮。 |
| **做法：parallel tools** | 有。同一次模型轮次中的工具调用可以并行执行。 | 没有，action 按顺序执行。 | 有。exclusive 调用形成 barrier，安全调用共享一个有上限的池。 |
| **做法：streaming** | 有。模型 token、工具调用与工具结果发生的当下就逐一送出。 | 没有。 | 有。流式 chunk 以 durable 事件写进 session log。 |

---

## 常见问题

- **没有停止条件：**一个 bug 或工具 loop 可能永远跑下去。用最大步数或 token 上限。
- **loop 中途 context overflow：**`messages[]` 只会变长。第 8 章加上 context 管理。
- **部分工具失败：**失败的工具仍必须返回一个 `tool_result`，模型才能复原。
- **结果遗失：**丢掉 assistant 的工具调用或工具结果任何一个，都会破坏 transcript。两者都要附加。

---

## 动手跑跑看

[`src/`](src/) 从这里开启整条链：

- [`loop.py`](src/loop.py)：内层 loop 与共享的 `messages[]`。
- [`demo.py`](src/demo.py)：两轮的联网 demo。第 2 轮依赖第 1 轮仍留在缓冲区里。
- [`test.py`](src/test.py)：针对工具 dispatch、最终文字与多轮状态的离线检查。

第 2 到 11 章会把这份 `src/` 带着往前走，持续演进 `loop.py`，并在每一章加上一个文件。

```bash
python sections/01-agent-loop/src/test.py         # offline checks, no key
uv run python sections/01-agent-loop/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code)：`QueryEngine.ts`、`query/`、`Tool.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py`、`exceptions.py`、`environments/local.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/architecture.md`、`docs/agent-lifecycle.md`、`docs/subsystems/core.md`、`packages/core/agent-loop/src/agent.ts`、`packages/core/agent/src/types.ts`。
- [learn-claude-code · s01 Agent Loop](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
