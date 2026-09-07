# 6 · Subagents

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Run a focused child loop and return only its result.

The main agent can hand work to a subagent: the delegating side is the parent, the one sent off is the child.

To the parent, this is just one tool call. But inside that call runs a complete agent loop.
The parent gives the child a prompt. The child gets a fresh `messages[]`, runs to completion, and returns its final answer.

This keeps side investigations out of the parent context. The parent does not need every file read or command result from the child. It usually needs the conclusion.

Without subagents, every investigation stays in the main transcript. Long runs become noisy, expensive, and harder for the model to follow.

---

## Mechanism

![Mechanism diagram](assets/06-subagents.png)

An `Agent` tool starts a child agent. The child has its own session and message list. It runs the same loop as the parent.

Only the child's final text comes back. Its transcript is discarded. File writes and shell side effects still happen in the working directory.

### New: the Agent tool

```python
def agent_tool(model, child_registry, parent_session):     # src/subagents.py
    def spawn(a):
        child = Session(mode=parent_session.mode,          # fresh context, inherited authority
                        allow_rules=set(parent_session.allow_rules))
        messages = [{"role": "user", "content": a["description"]}]   # the child's own conversation
        return run_turn(messages, model, child_registry, child)      # the loop, run again
    return Tool("Agent", spawn, is_read_only=True)
```

- `agent_tool` returns a normal tool.
- Its handler calls `run_turn()` with a new `Session`.
- The child's `messages[]` starts with only the child prompt.
- The child returns the text that `run_turn()` returns.

### How it integrates

The loop does not change. A subagent is just another tool handler that calls the loop.

Three properties matter:

- **Fresh context.** The child does not inherit the parent's transcript. The parent does not inherit the child's trace.
- **Inherited authority.** The child copies the parent's permission mode and allow rules. Context isolation is not permission isolation.
- **Recursion limit.** The demo omits `Agent` from the child registry, so a child cannot spawn another child.

---

## Per system

How each agent isolates a subproblem and returns the result.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **Pros** | A child context keeps the parent focused and the main transcript clean. | One seam spans in-process children, outside runtimes, and product CLIs. |
| **Cons** | The parent loses how the child got there. A thin summary means asking again. | Six backends and a resume manager, where one tool would do. |
| **Why** | The parent needs the conclusion, not every file the child read. | Delegation is a transport choice, so each backend registers under a name. |
| **How: spawn primitive** | The `Agent` tool. A subagent type picks a built-in persona. | One tool per registered backend: fresh child, fork, outside runtime, or CLI. |
| **How: context isolation** | Fresh child messages. A fork child cannot fork again. | A fresh child starts empty. A fork copies the parent's finished turns only. |
| **How: result return** | The text of the child's last message goes back. The transcript is dropped. | The last assistant message, plus optional output checked against a schema. |
| **How: resume** | Most agents resume. The parent sends a follow-up message. | Durable children queue follow-ups and reload from the log after a restart. |

---

## Failure modes

- **Lossy summary.** The child may compress too much. Ask it to write important findings to disk.
- **Runaway recursion.** Children spawning children can grow without bound. Omit the `Agent` tool from child registries or enforce a depth limit.
- **No child stop.** The child has the same halt risks as the parent. Give each child its own turn or token limit.
- **Assumed permission isolation.** A child still needs the normal permission gate. Do not skip it because the context is separate.
- **Orphaned async child.** A background child can finish after the parent moves on. Track it with a task record.

---

## Runnable

[`src/`](src/) carries 05 forward and adds:

- [`subagents.py`](src/subagents.py): the `Agent` tool.
- [`loop.py`](src/loop.py): unchanged from section 5.
- [`demo.py`](src/demo.py): the parent delegates a count to a child.
- [`test.py`](src/test.py): checks fresh context, inherited authority, and recursion fencing.

```bash
python sections/06-subagents/src/test.py         # offline checks, no key
uv run python sections/06-subagents/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `tools/AgentTool/AgentTool.tsx`, `runAgent.ts`, `resumeAgent.ts`, `forkSubagent.ts`, `builtInAgents.ts`, `tasks/LocalAgentTask/`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/subagent/subagent/src/index.ts`, `src/continuation.ts`, `packages/subagent/subagent-fork-in-process/README.md`,
  `packages/subagent/subagent-acp/README.md`, `docs/subsystems/subagent.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s06_subagent](https://github.com/shareAI-lab/learn-claude-code): section framing.
