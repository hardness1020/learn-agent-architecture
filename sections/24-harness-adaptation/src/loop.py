"""Agent loop (sections 1 to 13). Changed from section 11: background-task
completions are drained in at the start of a turn (section 13).

A tool may start slow work off-loop and return a handle immediately (section
13); its result arrives later as a <task_notification>. background.drain_into
folds any pending notifications into the next user turn before the model call.
Everything else is the section-11 loop: retry (section 11), prompt assembly
(section 10), memory (section 9), context.manage (section 8).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import background
import context
import permissions
import recovery
from hooks import Hooks
from tools import Registry, run_tool


@dataclass
class Session:
    """Mutable harness state that outlives a turn."""
    mode: str = permissions.DEFAULT
    allow_rules: set = field(default_factory=set)
    todos: list = field(default_factory=list)


def run_turn(messages, model, registry: Registry, session: Session, hooks: Hooks | None = None,
        approver=None, summarizer=None, memory=None, prompt=None, fallback_model=None,
        runtime=None, max_steps=20):
    hooks = hooks or Hooks()
    approver = approver or (lambda name, args: False)

    background.drain_into(messages, runtime)               # 13 · fold in any background completions

    if memory is not None:
        user_text = messages[-1]["content"]                # the new user turn (already appended)
        recalled = memory.recall(user_text)                # 9 · inject relevant memories (read-only)
        if recalled:
            messages[-1]["content"] = f"<system-reminder>\n{recalled}\n</system-reminder>\n\n{user_text}"

    for _ in range(max_steps):
        messages = context.manage(messages, summarizer=summarizer)   # 8 · keep context under the window
        system = prompt(registry, session) if prompt else None       # 10 · assemble from live state each turn
        response = recovery.with_retry(                              # 11 · retry / adapt / fall back
            lambda: model(messages, registry, system),
            on_overflow=lambda: _reactive_trim(messages),
            fallback_model=fallback_model)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            if memory is not None:
                memory.write(messages)                     # 9 · extract new memories at run end (write-only)
            return final_text(response)

        results = [_dispatch(b, registry, session, hooks, approver)
                   for b in response.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": results})

    raise RuntimeError("hit max_steps without end_turn")


def _reactive_trim(messages, keep_recent=4):
    """Last-resort in-place compaction when a call overflows: keep the first
    message and the recent tail, drop the middle, never stranding a tool_result."""
    if len(messages) <= keep_recent + 1:
        return
    cut = len(messages) - keep_recent
    while cut < len(messages) and _is_tool_result(messages[cut]):
        cut += 1
    del messages[1:cut]


def _is_tool_result(m):
    c = m.get("content")
    return m.get("role") == "user" and isinstance(c, list) and \
        any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def _dispatch(block, registry, session, hooks, approver):
    name, args = block.name, block.input
    tool = registry.get(name)
    res = lambda content: {"type": "tool_result", "tool_use_id": block.id, "content": content}
    if tool is None:
        return res(f"error: no tool {name!r}")

    blocked, args, msg = hooks.fire_pre(name, args)                         # 4 · PreToolUse
    if blocked:
        return res(msg)

    decision = permissions.decide(tool, session.mode, session.allow_rules)  # 3 · gate (live mode)
    if decision == "deny":
        return res(f"{name} not allowed in {session.mode} mode")
    if decision == "ask" and not approver(name, args):
        return res(f"{name} denied by user")

    out = res(run_tool(tool, args))
    hooks.fire_post(name, args, out)                                        # 4 · PostToolUse
    return out


def final_text(response):
    """The model's last words: concatenate its text blocks."""
    return "".join(b.text for b in response.content if b.type == "text")
