"""Subagents (section 6): spawn the agent loop again inside a tool call.

Introduced in section 6, then carried forward unchanged.

A subagent is `run()` (loop.py) invoked with a FRESH messages[]: the child
explores in its own context and only its final text returns to the parent. The
child's own tool calls still pass the gate (section 3), so isolating context
does not isolate authority. Recursion is fenced by curating the child's tools:
the child registry omits the Agent tool, so a child cannot spawn another.
Mirrors Claude Code's AgentTool + runAgent.ts (fresh initialMessages,
extractTextContent of the last message).
"""
from __future__ import annotations

from loop import Session, run_turn
from tools import Tool

DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {"description": {"type": "string"}},
    "required": ["description"],
}


def agent_tool(model, child_registry, parent_session, max_steps=20) -> Tool:
    """Agent: run a child loop on `description`, return only its final text."""
    def spawn(a):
        child = Session(mode=parent_session.mode,
                        allow_rules=set(parent_session.allow_rules))  # fresh context, inherited authority
        return run_turn([{"role": "user", "content": a["description"]}], model, child_registry, child, max_steps=max_steps)

    # spawning is read-only itself; the child's individual calls are what the gate sees
    return Tool("Agent", spawn,
                description="Delegate a subtask to a fresh subagent; returns only its final answer.",
                input_schema=DESCRIPTION_SCHEMA, is_read_only=True)
