"""Graph engineering (section 22): control flow as a directed graph.

Section 21 stacked loops around one agent. This section encodes the task
structure you already know into code: nodes do the work, edges pick the next
node, and the model runs only inside the nodes that need judgment.

nodes is a dispatch map (section 2). An edge is either a fixed node name
(deterministic) or a callable on the state (conditional, evaluated in code,
no model call). Cycles are allowed; the step budget is the ceiling (section
21). State is one dict threaded node to node; a node returns only the keys
it wants merged, not the whole record.

Mirrors the graphs named by the graph-engineering sources (nodes on a
determinism-to-agency scale, coded edges, cycles, agents as nodes) and
Claude Code's Workflow scripts (code routes, subagents run inside nodes).
"""
from __future__ import annotations

from loop import Session, run_turn
from permissions import DEFAULT

END = "END"


def run_graph(nodes, edges, state, start, budget=20):
    """Run nodes from start, following edges, until END or the budget.

    nodes: {name: fn(state) -> dict of state updates (or None)}.
    edges: {name: next name, or fn(state) -> next name}. No edge means END.
    Returns {"ok", "state", "trace"}; ok=False means the budget stopped a
    cycle: escalate (section 21), do not loop forever."""
    state = dict(state)
    trace = []
    node = start
    for _ in range(budget):                        # the ceiling: harness-enforced
        state.update(nodes[node](state) or {})     # a node returns only its updates
        trace.append(node)
        step = edges.get(node, END)
        node = step(state) if callable(step) else step   # a coded edge: no model call
        if node == END:
            return {"ok": True, "state": state, "trace": trace}
    return {"ok": False, "state": state, "trace": trace}   # budget spent: escalate


def agent_node(build_prompt, model, registry, key="output"):
    """A full inner loop (section 1) mounted as one node: agents as nodes.

    Each visit runs run_turn on a FRESH messages[] built from the state, so
    the node sees only what build_prompt passes it, not the whole run."""
    def node(state):
        text = run_turn([{"role": "user", "content": build_prompt(state)}],
                        model, registry, Session(mode=DEFAULT))
        return {key: text}

    return node
