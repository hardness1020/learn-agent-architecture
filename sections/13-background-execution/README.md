# 13 · Background execution

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Start slow work off the main loop and report back later.

Some operations take a long time: installs, builds, test suites, memory consolidation, or a subagent running its own loop.

The basic agent loop waits for tool calls to finish before calling the model again.

That is fine for fast reads. It is wasteful for slow work that can run while the agent does something else.

Background execution must:

1. Decide which operations can run without blocking.
2. Start them and return a handle immediately.
3. Track running, completed, failed, and killed states.
4. Send a completion message back into the loop later.

Without this layer, one slow command can freeze the whole agent.

---

## Mechanism

![Mechanism diagram](assets/13-background-execution.png)

There are three pieces:

1. An off-loop starter that returns a handle.
2. A runtime that tracks task state.
3. A queue that injects a completion notification on a later turn.

The loop does not wait for the slow work.

- Backgrounding is an execution option, not a special tool type.
- A backgrounded call returns a normal `tool_result` right away.
- The real completion arrives later as a separate notification.
- A whole subagent can run in the background.

### New: off-loop start and notification drain

`start` runs work on a worker thread and returns a task id:

```python
def start(self, fn):                                   # src/background.py; returns immediately
    self._next += 1
    tid = self._next
    self._state[tid] = "running"
    def work():
        try:
            self._finish(tid, "completed", str(fn()))  # enqueues a <task_notification>
        except Exception as e:
            self._finish(tid, "failed", f"{type(e).__name__}: {e}")
    threading.Thread(target=work, daemon=True).start()
    return tid
```

`drain_into` folds completed notifications into the next user turn:

```python
def drain_into(messages, runtime):                     # src/background.py
    notes = runtime.drain() if runtime else []
    if notes and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = "\n".join(notes) + "\n\n" + messages[-1]["content"]
```

`backgroundable` wraps any tool and adds `run_in_background` to its schema:

```python
def backgroundable(tool, runtime):                     # src/background.py; wraps ANY tool
    def run(a):
        if a.get("run_in_background"):
            inner = {k: v for k, v in a.items() if k != "run_in_background"}
            tid = runtime.start(lambda: tool.run(inner))
            return f"started background task {tid} ({tool.name}); ..."
        return tool.run(a)
    ...
    return replace(tool, run=run, ...)
```

The wrapper also sets what the model gets back. A backgrounded call only starts the work. It returns a task id, and the result arrives later as its own event.
Name and describe slow tools that way (`initiate_export`, not `export`). Then the model reads the immediate `tool_result` as a receipt, not as the answer.

### How it integrates

The loop drains pending completions at the start of a turn:

```python
background.drain_into(messages, runtime)               # src/loop.py
```

The one-tool-call-to-one-tool-result rule still holds. A late completion is not a delayed `tool_result` for the old `tool_use_id`. It is a new notification message.

### Further reading

None of this is in `src/`. It comes from ai-agent-book, and is not confirmed of the systems in the table.

**Interrupts and safe points.** Some input cannot wait for the running tool call to finish.
A user correction, a cancel, or an alert can land mid-call. One answer is to make every inbound input an event on one stream.
The loop reads that stream only at a safe point, the gap between a finished tool result and the next model call.
Writing into the middle of a call would break the transcript, so events wait for the gap.

How urgent an event is decides which gap it waits for:

- **Queue.** Wait for the next gap. This is the default for completions and low priority notices.
- **Cancel.** Stop the running call to open a gap now. Use it when a correction makes the running work pointless.
- **Parallel.** Run the event in a side loop and leave the main loop alone.

Sorting events is itself cheap. A small model can do it, so triage costs one call per event.

**Interrupt placeholders.** A cancel needs one more step before the transcript is legal again.
The stopped call left a `tool_use` block with no `tool_result`, and the next model call needs that pair closed.
ai-agent-book closes it right away. It writes a placeholder `tool_result` on the same id that says the call was interrupted.
That does not break the no-reuse rule above. The placeholder closes the pair now. The real result still arrives later as its own notification.
The placeholder is the book author's own design. No other source describes it.

---

## Per system

How each agent moves work off the loop and reports completion.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **Pros** | Throughput improves and idle waits go away. A plain wait blocks nothing. | One registry serves shell, terminal, and child agents alike. |
| **Cons** | Results can arrive late and out of order. The runtime tracks state and cleanup. | Waking an idle agent spends turns, so it needs a budget. |
| **Why** | One slow command must not freeze the whole agent. | A finished job must reach the model without the model polling. |
| **How: off-loop primitive** | Background shell and agent tasks. The subprocess runs on, output redirected. | Any tool takes a run-in-background flag and returns a job id. |
| **How: notification** | A `<task_notification>` message through one shared queue. | One notice per job. First finish wins, and duplicates are suppressed. |
| **How: re-entry** | The queue drains between turns, at `now`, `next`, and `later` priorities. | A busy agent gets it next step. An idle one is woken, within a cap. |

---

## Failure modes

- **Interactive prompt stalls.** A background command waits for input. Detect prompt-like output and notify the model to kill or rerun non-interactively.
- **Lost completion.** A finished task never reaches the loop. Send completion through one shared queue and mark tasks notified.
- **Mispaired notification.** Reusing the old `tool_use_id` breaks the transcript. Use standalone notification text.
- **Side effect after a kill.** A timeout or a cancel does not tell you whether the call landed. A blind retry can charge twice. Query state first, or send an idempotency key.
- **Batched events dilute attention.** One drain can fold several notifications into one turn. The model then answers only the last one. Number the events and add a summary line.
- **Too much concurrency.** Many background tasks can exhaust resources. Add kill paths and limits.
- **Process leak on exit.** Background work can outlive the session. Register cleanup.

---

## Runnable

[`src/`](src/) carries 12 forward and adds:

- [`background.py`](src/background.py): a runtime, notification queue, `drain_into`, and `backgroundable`.
- [`loop.py`](src/loop.py): drains pending notifications before the model call.
- [`test.py`](src/test.py): checks start, failure, drain, and background subagents.
- [`demo.py`](src/demo.py): launches a subagent in the background and reads its result later.

```bash
python sections/13-background-execution/src/test.py         # offline checks, no key
uv run python sections/13-background-execution/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code task sources](https://github.com/yasasbanukaofficial/claude-code): `tasks/LocalShellTask/`, `tasks/DreamTask/`.
- [Claude Code tool and queue sources](https://github.com/yasasbanukaofficial/claude-code):
  `tools/BashTool/BashTool.tsx`, `tools/SleepTool/prompt.ts`, `utils/task/framework.ts`, `utils/messageQueueManager.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/jobs/jobs/src/index.ts`, `packages/jobs/jobs-local/src/index.ts`, `packages/jobs/tool-jobs/README.md`,
  `docs/subsystems/jobs.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s13_background_tasks](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, Chinese original canonical.
  Idempotency and cancel semantics, initiate-and-complete naming, event triage at safe points, interrupt placeholders, batched-event attention.
