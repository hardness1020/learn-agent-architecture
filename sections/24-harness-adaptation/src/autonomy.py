"""Autonomy (section 18): an outer loop around the agent loop (section 1) so a
teammate keeps working with no human prompt. Introduced in section 18, extending
section 17's run_teammate with the task board.

The inner loop is the normal run_turn (section 1). When it ends, the agent does
not return; it idles and polls three sources in priority order:

  1. A shutdown request (section 17): confirm it and stop. Checked first, so a
     flood of peer chatter cannot starve a stop.
  2. An inbox message (section 16): lead before peers, folded into one prompt.
  3. The task board (section 12): claim the first pending, unowned, unblocked
     task. The claim is lock-serialized in TaskStore, so two idle agents cannot
     both win the same task; this only proposes a candidate to try.

Whatever the poll finds becomes the next prompt and the inner loop runs again.
The loop and the subagent path do not change; autonomy only wraps them.

ponytail: max_idle_polls bounds the idle wait so the demo and test terminate; a
real teammate polls until shutdown or abort, with no upper bound.
"""
from __future__ import annotations

import time

from mailbox import _fold
from protocols import Protocol

POLL_INTERVAL = 0.05    # seconds between idle polls; short so demo/test do not wait


def claim_next(store, me):
    """Scan the board oldest-first and claim the first pending, unowned task.
    Returns the claimed task or None. TaskStore.claim (section 12) rejects a
    blocked task and is lock-serialized, so a claim race has exactly one winner."""
    for t in store.list():
        if t["status"] == "pending" and t["owner"] is None:
            got = store.claim(t["id"], me)
            if got["ok"]:
                return got["task"]
            # not ok: another agent won it or it just became blocked, so try the next
    return None


def _is(msg, type_):
    content = msg["content"]
    return isinstance(content, dict) and content.get("type") == type_


def next_action(proto, team, store, me):
    """One idle poll. Drain the inbox once, then pick the next thing to do in
    priority order: a shutdown request (confirm and stop), a peer/lead message,
    or a claimed task. Returns (kind, payload), or None when there is nothing to
    do. Shutdown is checked before chat so peer traffic cannot starve a stop."""
    inbox = team.drain(me)
    shutdown = next((m for m in inbox if _is(m, "shutdown_request")), None)
    if shutdown is not None:
        proto.reply(shutdown, "shutdown_approved")     # inner loop already flushed at end_turn
        return ("shutdown", shutdown["content"].get("reason"))
    chat = [m for m in inbox if isinstance(m["content"], str)]
    if chat:
        chat.sort(key=lambda m: m["from"] != "lead")   # lead before peers; sort is stable
        return ("message", _fold(chat))
    task = claim_next(store, me)
    if task is not None:
        return ("task", task)
    return None                                         # idle: caller sleeps and polls again


def task_prompt(task):
    """Turn a claimed task into the next user prompt for the inner loop."""
    return (f"You claimed task {task['id']}: {task['subject']}. "
            f"Do it, then call TaskUpdate to mark it completed.")


def run_teammate(team, store, me, lead, work, *, max_idle_polls=None):
    """Section 17's run_teammate (protocols.py) idles on an empty inbox; this adds
    the task board, so an idle teammate claims a task instead of just waiting. Run
    the inner agent loop on a prompt, then idle-poll for the next thing to do.
    `work(prompt, task)` runs one inner loop (section 1) to end_turn on the claimed
    task (None on a message-driven turn). Two stop modes,
    both the worker's own decision at end_turn. max_idle_polls=None: idle until a
    shutdown handshake lands (a long-lived worker on a dynamic board, kept warm
    for the coordinator to end it; section 17). A small int: stop after that many
    empty polls (a worker on a known, finite board decides the work is gone and
    winds itself down). Returns 'shutdown' or 'drained'."""
    proto = Protocol(team, me)
    prompt = claimed = None
    idle = 0
    while True:
        if prompt is not None:
            work(prompt, claimed)                       # inner loop (section 1), runs to end_turn
            prompt = claimed = None
            team.send(me, lead, {"type": "idle", "reason": "available"})   # sendIdleNotification
        action = next_action(proto, team, store, me)
        if action is None:                              # nothing to do this pass
            idle += 1
            if max_idle_polls is not None and idle >= max_idle_polls:
                return "drained"                        # only a lead-less single worker hits this
            time.sleep(POLL_INTERVAL)
            continue
        idle = 0
        kind, payload = action
        if kind == "shutdown":
            return "shutdown"
        if kind == "task":
            prompt, claimed = task_prompt(payload), payload
        else:                                           # message: no task to thread through
            prompt = payload
