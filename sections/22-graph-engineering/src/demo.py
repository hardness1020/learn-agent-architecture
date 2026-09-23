"""Section 22 demo: one routed graph run. A code node classifies the task,
a coded edge routes it to a specialist, an agent node answers inside the
graph, a checker node grades it, and a failed verdict cycles back with
feedback until the step budget stops it.

run_turn is unchanged; nodes wrap it (section 1). The checker is section
21's agent_checker mounted as a node, so the evaluator-optimizer shape is
two nodes and one backward edge. The classify node and the check edge are
plain code, so that part of the routing spends no tokens.

The edge out of classify is the one branch that needs judgment, so it reads
a probability instead (decide.py). With TYPESAFE_API_KEY set it asks a
decision model; without one it reads a recorded answer, so the demo still
needs only the Anthropic key. Either way an unsure or failed answer stops
the run for a person rather than letting it through.

    uv run python sections/22-graph-engineering/src/demo.py   (needs ANTHROPIC_API_KEY; see root README)
"""
import os

from anthropic import Anthropic
from dotenv import load_dotenv

from decide import decision_edge, fixture_asker, typesafe_asker
from graph import END, agent_node, run_graph
from tools import Registry, Tool
from verify import agent_checker

load_dotenv(override=True)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
RUBRIC = "The output is exactly one number and nothing else."
TASK = "What is 27 + 15? Use the add tool. Answer with just the number."
GATE = "Would running this task destroy work that cannot be recovered?"
GATE_CRITERIA = {"safe": "Reads, computes, or writes something reversible.",
                 "destructive": "Deletes, overwrites, or publishes something that cannot be undone."}

ADD = Tool(name="add", run=lambda a: a["x"] + a["y"], description="Add two integers.",
           input_schema={"type": "object",
                         "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                         "required": ["x", "y"]},
           is_read_only=True)


def demo():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("22 graph-eng: set ANTHROPIC_API_KEY to run the live demo (offline checks: test.py)")
        return

    client = Anthropic(base_url=os.environ.get("ANTHROPIC_BASE_URL") or None)

    def model(messages, registry, system):
        kwargs = {"system": system} if system else {}
        return client.messages.create(model=MODEL, messages=messages,
                                      tools=registry.schemas(), max_tokens=256, **kwargs)

    math_reg = Registry()
    math_reg.register(ADD)
    prompt = lambda s: s["task"] + s["feedback"]
    check = agent_checker(RUBRIC, model)               # section 21's checker, now a node

    def check_node(state):
        v = check(state["task"], state["output"])
        fb = "" if v["passed"] else (f"\n\nA prior attempt was rejected by review.\n"
                                     f"Attempt:\n{state['output']}\nWhy it failed: {v['reason']}\n"
                                     f"Fix that and answer again.")
        return {"verdict": v, "feedback": fb}

    live = bool(os.environ.get("TYPESAFE_API_KEY"))
    ask = (typesafe_asker(GATE, GATE_CRITERIA, harmful="destructive") if live
           else fixture_asker({TASK: (0.02, 0.93)}))  # recorded, so one key still runs this

    nodes = {
        "classify": lambda s: {"route": "math" if any(c.isdigit() for c in s["task"]) else "prose"},
        "math": agent_node(prompt, model, math_reg),   # a full agent run as one node
        "prose": agent_node(prompt, model, Registry()),
        "check": check_node,
        "escalate": lambda s: {"output": "stopped for a person: the gate was not sure"},
        "refuse": lambda s: {"output": "refused: the gate read this as destructive"},
    }
    edges = {
        "classify": decision_edge(ask, on={"allow": lambda s: s["route"],   # the band routes
                                           "ask": "escalate", "deny": "refuse"}),
        "math": "check",
        "prose": "check",
        "check": lambda s: END if s["verdict"]["passed"] else s["route"],   # the cycle
    }

    r = run_graph(nodes, edges, {"task": TASK, "feedback": ""}, start="classify", budget=5)
    print("22 graph-eng: gate:", "decision model" if live else "recorded answer")
    print("22 graph-eng: trace:", " -> ".join(r["trace"]))
    print("22 graph-eng:", r["state"]["output"] if r["ok"] else "budget spent, escalating to a human")


if __name__ == "__main__":
    demo()
