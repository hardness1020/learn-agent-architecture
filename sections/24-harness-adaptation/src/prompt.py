"""System prompt assembly (section 10): build the system prompt each turn from
named sections chosen by live state, not one hardcoded string.

Introduced in section 10, then carried forward unchanged.

A section is static (always present) or dynamic (compute(state) -> str | None).
assemble() runs every section against current state, drops the Nones, and joins.
A section is included by state, not keywords: the env section appears only with a
cwd, the mcp section only when a server is connected. Recalled memory does NOT
live here; it rides in the message as a <system-reminder> (section 9), so it
never touches the system-prompt cache.

Caching: within a conversation the system prompt is stable, so the demo enables
automatic caching, one top-level cache_control that caches the whole prompt plus
the growing messages and advances as the conversation grows (see demo.py).
Mirrors Claude Code's getSystemPrompt + resolveSystemPromptSections.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Section:
    name: str
    compute: Callable    # (state) -> str | None ; static sections ignore state, returning a constant


def static(name, text) -> Section:
    return Section(name, lambda _state: text)


def assemble(sections, state) -> str:
    """The system prompt for this turn: run every section, drop the Nones, join."""
    parts = (s.compute(state) for s in sections)
    return "\n\n".join(p for p in parts if p is not None)


# A demonstrative section list, the strip-down's stand-in for Claude Code's
# getSimpleIntroSection / getUsingYourToolsSection / mcp_instructions / etc.
DEMO_SECTIONS = [
    static("intro", "You are a tiny agent. Use the provided tools to answer. Be brief."),
    static("rules", "Rules: prefer tools over guessing; never invent file contents."),
    Section("tools", lambda s: "Tools: " + ", ".join(s["tools"]) if s.get("tools") else None),
    Section("env", lambda s: f"cwd: {s['cwd']}" if s.get("cwd") else None),
    Section("mcp", lambda s: "MCP servers connected; extra tools may appear." if s.get("mcp") else None),
]
