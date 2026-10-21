"""Planning and todos (section 5): a TodoWrite tool and plan mode. Introduced
in section 5, then carried forward unchanged. Both tools mutate the Session
(loop.py): TodoWrite records the plan, ExitPlanMode flips the mode once the
plan is approved. Mirrors Claude Code's TodoWriteTool and Enter/ExitPlanMode.
"""
from __future__ import annotations

import permissions
from tools import Tool

TODOS_SCHEMA = {
    "type": "object",
    "properties": {
        "todos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"step": {"type": "string"}, "status": {"type": "string"}},
                "required": ["step", "status"],
            },
        },
    },
    "required": ["todos"],
}


def todo_tool(session) -> Tool:
    """TodoWrite: record the plan as a list of steps on the session."""
    def write(a):
        session.todos = list(a["todos"])
        done = sum(1 for t in session.todos if t.get("status") == "completed")
        return f"{len(session.todos)} todos ({done} done)"
    return Tool("TodoWrite", write,
                description="Record the plan as a checklist of {step, status} items.",
                input_schema=TODOS_SCHEMA, is_read_only=True)   # agent state only, no side effect


def exit_plan_mode_tool(session, to_mode=permissions.ACCEPT_EDITS) -> Tool:
    """ExitPlanMode: once the plan is approved, leave plan mode for `to_mode`."""
    def exit_plan(_a):
        session.mode = to_mode
        return f"plan approved, mode now {to_mode}"
    return Tool("ExitPlanMode", exit_plan,
                description="Present the plan and leave plan mode once the user approves.")
