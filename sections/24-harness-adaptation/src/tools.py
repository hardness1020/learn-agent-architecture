"""Tool runtime (section 2): the Tool contract, a registry, dispatch, and
parallel execution of concurrency-safe calls. Introduced in section 2, then
carried forward unchanged.

A Tool carries the Anthropic input_schema it advertises plus the handler that
runs it; the Registry emits the tools list for the model. Mirrors Claude Code's
Tool.ts (name, isReadOnly, isConcurrencySafe) and the partition-then-run step.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

NO_INPUT = {"type": "object", "properties": {}}   # schema for a tool that takes no arguments


@dataclass
class Tool:
    name: str
    run: Callable[[dict], Any]
    description: str = ""
    input_schema: dict = field(default_factory=lambda: dict(NO_INPUT))
    is_read_only: bool = False
    is_concurrency_safe: bool = False   # may share a parallel batch
    is_edit: bool = False               # a file edit; read by the permission gate (section 3)


class Registry:
    """Name to Tool. Adding a tool is registering one handler."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        """The tools list advertised to the model (Anthropic format)."""
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in self._tools.values()]


def run_tool(tool: Tool, tool_input: dict) -> str:
    """Execute one tool, turning any failure into a string result (never raises)."""
    try:
        return str(tool.run(tool_input))
    except Exception as e:  # ponytail: failure is a result fed back, not a crashed loop
        return f"error: {type(e).__name__}: {e}"


def run_concurrently(registry: Registry, calls: list[dict]) -> list[str]:
    """Dispatch a turn's tool calls; concurrency-safe ones share one parallel
    batch. Output order matches input. The loop dispatches sequentially for
    readability; this is the batching primitive the real runtime applies. Each
    call is {"name": ..., "input": ...}."""
    tools = [registry.get(c["name"]) for c in calls]
    out: dict[int, str] = {}

    safe = [i for i, t in enumerate(tools) if t and t.is_concurrency_safe]
    if safe:
        with ThreadPoolExecutor(max_workers=len(safe)) as pool:
            for i, res in zip(safe, pool.map(lambda i: run_tool(tools[i], calls[i].get("input", {})), safe)):
                out[i] = res

    for i, t in enumerate(tools):
        if i not in out:
            out[i] = f"error: no tool {calls[i]['name']!r}" if t is None else run_tool(t, calls[i].get("input", {}))

    return [out[i] for i in range(len(calls))]
