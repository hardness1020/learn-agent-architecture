"""Background execution (section 13): start slow work off the loop, return a
handle now, deliver the result later through a notification queue.

Introduced in section 13, then carried forward unchanged.

Runtime.start() runs any thunk on a worker thread and returns at once with a
task id (the tool's placeholder tool_result, section 1). On completion the
worker enqueues a <task_notification>; drain_into() pulls those notes on a later
turn and prepends them to the next user message. One tool_use still gets exactly
one tool_result, the completion is a separate event.

Backgrounding is a property of execution, not of one tool: backgroundable()
wraps ANY tool so the model can run it off-loop with run_in_background. A shell
command, a subagent (section 6), a build, or a memory-consolidation pass all
take the same path. Mirrors Claude Code's task framework backing LocalShellTask,
DreamTask, and friends through one messageQueueManager drain.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import replace

from tools import Tool


class Runtime:
    """Tracks background tasks and queues their completion notifications."""

    def __init__(self):
        self._next = 0
        self._state: dict[int, str] = {}        # task id -> running | completed | failed
        self._notes: queue.Queue = queue.Queue()

    def start(self, fn):
        """Run any thunk on a worker thread; return a task id immediately (never blocks)."""
        self._next += 1
        tid = self._next
        self._state[tid] = "running"

        def work():
            try:
                self._finish(tid, "completed", str(fn()))
            except Exception as e:              # a failed background task still reports back
                self._finish(tid, "failed", f"{type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()
        return tid

    def state(self, tid):
        return self._state.get(tid)

    def drain(self):
        """Every completion notification enqueued so far, oldest first; empties the queue."""
        out = []
        while True:
            try:
                out.append(self._notes.get_nowait())
            except queue.Empty:
                return out

    def _finish(self, tid, status, output):
        self._state[tid] = status
        self._notes.put(f"<task_notification>task {tid} {status}: {output}</task_notification>")


def drain_into(messages, runtime):
    """Prepend any completed-task notifications to the latest user message, so a
    background result surfaces on the next turn (Claude Code's queue drain)."""
    notes = runtime.drain() if runtime else []
    if notes and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = "\n".join(notes) + "\n\n" + messages[-1]["content"]
    return messages


def backgroundable(tool: Tool, runtime: Runtime) -> Tool:
    """Wrap any tool so the model can run it off-loop with run_in_background.
    A shell command, a subagent (section 6), a build, or a long compute task all
    take the same path: start the thunk, return a handle, notify on completion."""
    def run(a):
        if a.get("run_in_background"):
            inner = {k: v for k, v in a.items() if k != "run_in_background"}
            tid = runtime.start(lambda: tool.run(inner))
            return f"started background task {tid} ({tool.name}); its result arrives in a later message"
        return tool.run(a)

    schema = {**tool.input_schema,
              "properties": {**tool.input_schema.get("properties", {}),
                             "run_in_background": {"type": "boolean"}}}
    return replace(tool, run=run, input_schema=schema,                  # keeps is_read_only etc.
                   description=tool.description + " Set run_in_background for slow work.")
