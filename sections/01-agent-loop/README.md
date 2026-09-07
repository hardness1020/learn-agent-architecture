# 1 · Agent Loop

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> One loop keeps calling the model until it answers or asks for a tool.

A raw model call is one-shot. You send messages and get one response.

An agent needs another step. It must run the tool the model asked for, append the result, and call the model again. The same `messages[]` must keep growing across the turn.

The loop must:

1. Keep conversation state across calls.
2. Detect tool use versus a final answer.
3. Run requested tools and append the results.
4. Call the model again until it stops.

Without this loop, the model can reason about actions but cannot act. If the loop is wrong, it either stops too early or runs forever.

---

## Mechanism

![Mechanism diagram](assets/01-agent-loop.png)

There are two loops over one `messages[]`.

Picture a chat window. You ask "What is the weather in Taipei? Should I take an umbrella?"
The model may first call a weather tool, then call a rain-chance tool once it sees the result, and only then reply.
**So within one turn the model is often called several times, with tool calls in between.**
That whole stretch, from your question to the final answer, is the inner loop: one user turn.
It calls the model, checks `stop_reason`, runs tools if needed, appends results, and repeats until the model gives its answer for this turn.

Then you ask "What about tomorrow?" in the same window. That is a new turn.
The outer loop is what strings turn after turn into one conversation.
Each new turn is appended to the same `messages[]`, so when the model answers "tomorrow" it still sees that you asked about Taipei.

The inner loop is one turn over a `messages[]` owned by the caller:

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

- `run_turn()` in [`src/loop.py`](src/loop.py) is the inner loop.
- `messages` is the shared state in the Anthropic Messages format.
- `max_steps` is the safety limit for a runaway loop.
- `run_tool(name, input)` resolves the tool, runs it, and returns text for a `tool_result`.
- `model()` in [`src/demo.py`](src/demo.py) is one `client.messages.create` call. The loop does not depend on one provider.

The outer loop appends one user message per turn and keeps the buffer:

```python
messages = []                                        # src/demo.py · the conversation, owned by the caller
for user_text in turns:                              # the outer loop: one iteration per user turn
    messages.append({"role": "user", "content": user_text})
    reply = run_turn(messages, model)                # appends in place; turn N sees turns 1..N-1
```

Two `stop_reason` values drive the loop:

- `tool_use`: run the tools, append results, and call the model again.
- `end_turn`: return the final answer. The demo stops on any value that is not `tool_use`.

`messages[]` is the whole conversation memory for this session. Tool results and assistant replies both go into it. The next model call reasons over that full state.

This bare loop has no permission gate. Section 3 adds that gate before tool execution.

---

## Per system

How each agent owns the loop and decides when to stop.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Streams progress, gates side effects, runs tools in parallel. | A tiny loop, easy to read and audit. | Swappable loop, interceptable phases, a log that replays. |
| **Cons** | The loop sits inside a larger runtime. | No side-effect gate, no streaming, no parallel tools. | The most moving parts. Needs turn, step, and inbox vocabulary. |
| **Why** | Keep one core branch, add features around it. | A minimal loop is the point. The environment, not the model, detects completion. | The loop is one plugin among peers. |
| **How: loop driver** | An async generator. Tools plug in via one contract. | A while loop. Ask the model for a command, run it. | A swappable plugin over a durable event log. |
| **How: stop signal** | `stop_reason: end_turn`. | The environment sees a submit marker and appends `role: "exit"`. | Nothing owed, no checkpoint block, or a turn-ending result. |
| **How: parallel tools** | Yes. Calls in one model turn can run in parallel. | No. Actions run in order. | Yes. Exclusive calls form barriers; safe calls share a bounded pool. |
| **How: streaming** | Yes. Yields model tokens, tool calls, and tool results as they happen. | No. | Yes. Stream chunks land in the session log as durable events. |

---

## Failure modes

- **No stop condition.** A bug or tool loop can run forever. Use a max-step or token limit.
- **Context overflow mid-loop.** `messages[]` only grows. Section 8 adds context management.
- **Partial tool failure.** A failed tool must still return a `tool_result`, so the model can recover.
- **Lost results.** Dropping either the assistant tool call or the tool result breaks the transcript. Append both.

---

## Runnable

[`src/`](src/) starts the chain with:

- [`loop.py`](src/loop.py): the inner loop and the shared `messages[]`.
- [`demo.py`](src/demo.py): a two-turn live demo. Turn 2 depends on turn 1 staying in the buffer.
- [`test.py`](src/test.py): offline checks for tool dispatch, final text, and multi-turn state.

Sections 2 to 11 carry this `src/` forward, evolving `loop.py` and adding one file per section.

```bash
python sections/01-agent-loop/src/test.py         # offline checks, no key
uv run python sections/01-agent-loop/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `QueryEngine.ts`, `query/`, `Tool.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`, `exceptions.py`, `environments/local.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/agent-lifecycle.md`, `docs/subsystems/core.md`, `packages/core/agent-loop/src/agent.ts`, `packages/core/agent/src/types.ts`.
- [learn-claude-code · s01 Agent Loop](https://github.com/shareAI-lab/learn-claude-code): section framing.
