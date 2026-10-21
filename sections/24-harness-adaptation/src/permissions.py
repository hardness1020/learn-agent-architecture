"""Permission gate (section 3): the allow / ask / deny decision between a
model's tool request and execution. Introduced in section 3, then carried
forward unchanged. Modes mirror Claude Code's types/permissions.ts.
"""
from __future__ import annotations

DEFAULT, ACCEPT_EDITS, PLAN, BYPASS = "default", "acceptEdits", "plan", "bypassPermissions"


def decide(tool, mode: str, allow_rules: set) -> str:
    """Return 'allow', 'ask', or 'deny' for running `tool` under `mode`."""
    if mode == BYPASS:
        return "allow"                       # user opted out of the gate entirely

    if mode == PLAN:
        if tool.is_read_only:
            return "allow"                   # reading is fine while planning
        if tool.name == "ExitPlanMode":
            return "ask"                     # the plan-approval handshake (section 5)
        return "deny"                        # no side effects until the plan is approved

    if tool.is_read_only or tool.name in allow_rules:
        return "allow"
    if mode == ACCEPT_EDITS and tool.is_edit:
        return "allow"                       # edits pre-approved for this session

    return "ask"                             # default: a human decides
