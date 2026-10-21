"""MCP / plugins / channels (section 19): reach outside the harness.

connect() discovers an external server's tools (the MCP tools/list step) and
wraps each as a runtime Tool (section 2), namespaced mcp__<server>__<tool>, so
they merge into the one pool the loop dispatches. The loop, permission gate
(section 3), and dispatch do not change: an MCP tool is just a Tool whose run()
calls out over a transport instead of running in-process.

An MCP annotation (readOnlyHint / destructiveHint) rides along and becomes the
permission hint the gate reads, so a server declares how risky each tool is.

merge_servers() layers plugin/user/project/local config by precedence, the way
a plugin contributes servers alongside user and project config.

wrap_channel() turns a server push into a tagged message folded into the next
turn, so Slack, Discord, or SMS ride the same protocol as a two-way surface.
gate_inbound() runs that push through gates first: an inbound channel message is
untrusted input, so a gate may drop or rewrite it before the loop ever sees it
(Hermes' pre_gateway_dispatch hook, fired before auth on every incoming message).

Mirrors Claude Code services/mcp/: buildMcpToolName namespaces each tool,
normalizeNameForMCP sanitizes it, the readOnlyHint annotation drives the
permission hint, config.ts merges by precedence, and channelNotification.ts
wraps a push in CHANNEL_TAG.
"""
from __future__ import annotations

import re

from tools import NO_INPUT, Tool

_BAD = re.compile(r"[^a-zA-Z0-9_-]")             # the API's allowed name charset
CHANNEL_TAG = "channel"
PRECEDENCE = ("plugin", "user", "project", "local")   # low to high; later wins


def normalize(name: str) -> str:
    """normalizeNameForMCP: any char outside [a-zA-Z0-9_-] becomes _."""
    return _BAD.sub("_", name)


def tool_name(server: str, tool: str) -> str:
    """buildMcpToolName: mcp__<server>__<tool>, so two servers never collide."""
    return f"mcp__{normalize(server)}__{normalize(tool)}"


def wrap(server: str, spec: dict, call) -> Tool:
    """One discovered tool spec -> a runtime Tool bound to the server.

    call(tool, args) reaches the server over its transport. The MCP annotations
    map to the permission hints the gate (section 3) reads; a read-only tool may
    also share a parallel batch (section 2)."""
    ann = spec.get("annotations", {})
    read_only = bool(ann.get("readOnlyHint"))
    bare = spec["name"]
    return Tool(
        name=tool_name(server, bare),
        run=lambda args, _t=bare: call(_t, args),        # dispatch calls out over the transport
        description=spec.get("description", ""),
        input_schema=spec.get("inputSchema") or dict(NO_INPUT),
        is_read_only=read_only,
        is_concurrency_safe=read_only,                   # reads are safe to run in parallel
    )


def connect(server: str, conn) -> list[Tool]:
    """Discover a server's tools and wrap each as a namespaced Tool.

    conn is a live transport (stdio / http / sse in production; in-process here):
    .list_tools() -> tool specs, .call(tool, args) -> result. Merge the returned
    Tools into the loop's Registry and they dispatch like any built-in."""
    return [wrap(server, spec, conn.call) for spec in conn.list_tools()]


def merge_servers(*layers: dict) -> dict:
    """Layer server config by precedence plugin < user < project < local.

    Each layer maps server name -> config; a later layer overrides an earlier one
    for the same name. This is how a plugin's servers combine with user and
    project config (config.ts)."""
    merged: dict = {}
    for scope in PRECEDENCE:
        for layer in layers:
            merged.update(layer.get(scope, {}))
    return merged


def wrap_channel(source: str, payload: str) -> str:
    """A server push (notifications/claude/channel) becomes a tagged message the
    loop folds into the next turn (section 13's queue, section 16's inbox)."""
    return f'<{CHANNEL_TAG} source="{source}">{payload}</{CHANNEL_TAG}>'


def gate_inbound(source: str, payload: str, gates=()) -> str | None:
    """Run an inbound channel message through gates before it becomes a turn.

    Each gate(source, payload) may return {'drop': True} to discard the message
    or {'rewrite': str} to replace its payload; anything else passes it through.
    Returns the tagged message for the loop, or None when a gate dropped it.
    A channel is an open door, so the gate runs before the model ever reads the
    text (Hermes: pre_gateway_dispatch, skip / rewrite / allow)."""
    for gate in gates:
        out = gate(source, payload) or {}
        if out.get("drop"):
            return None
        if out.get("rewrite") is not None:
            payload = out["rewrite"]
    return wrap_channel(source, payload)
