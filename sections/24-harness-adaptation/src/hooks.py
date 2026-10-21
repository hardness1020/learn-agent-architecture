"""Hooks (section 4): PreToolUse / PostToolUse interception around tool calls.
Introduced in section 4, then carried forward unchanged.

A PreToolUse hook may block a call or rewrite its input; it cannot grant
permission a call lacks (so hooks here only deny or modify, never allow).
PostToolUse hooks observe results. Mirrors Claude Code's types/hooks.ts.
"""
from __future__ import annotations

PRE_TOOL_USE, POST_TOOL_USE = "PreToolUse", "PostToolUse"


class Hooks:
    def __init__(self):
        self._hooks = {PRE_TOOL_USE: [], POST_TOOL_USE: []}

    def on(self, event, fn):
        self._hooks[event].append(fn)

    def fire_pre(self, name, args):
        """Run PreToolUse hooks. Returns (blocked, args, message); a hook may
        rewrite args or, by returning {'deny': True}, block the call."""
        for fn in self._hooks[PRE_TOOL_USE]:
            out = fn(name, args) or {}
            if out.get("updated_args"):
                args = out["updated_args"]
            if out.get("deny"):
                return True, args, out.get("message", "blocked by hook")

        return False, args, ""

    def fire_post(self, name, args, result):
        for fn in self._hooks[POST_TOOL_USE]:
            fn(name, args, result)
