"""Section 22 offline checks, no key, no network.

test_chain(): fixed edges run nodes in order; each node's updates merge into
the state the next node reads. A node with no edge ends the graph.

test_route(): a conditional edge routes on the state. The branch not taken
never runs, and the edge itself is plain code, no model call.

test_cycle_budget(): a check node that never passes cycles back to the
worker until the step budget stops it with ok=False. The ceiling is the
harness's range(), not the model's judgment (section 21).

test_agent_node(): agent_node runs the inner loop on a fresh messages[]
built from the state and merges its text back under one key.

test_band(): route turns (probability, confidence) into allow, ask, or deny;
nothing labelled dangerous is ever allowed; a missing answer falls back to
ask, so a broken decision layer never grants a branch.

test_monotonic(): raising the probability never loosens the verdict. This
catches thresholds that were swapped or that overlap, and needs no labels.

test_decision_edge(): a probability-reading edge picks the next node, on a
recorded fixture, and interrupts less than a stated share of normal traffic.

    python sections/22-graph-engineering/src/test.py
"""
from decide import decision_edge, fixture_asker, route
from graph import END, agent_node, run_graph


def test_chain():
    trace_state = run_graph(
        nodes={"a": lambda s: {"seen": s["seen"] + ["a"]},
               "b": lambda s: {"seen": s["seen"] + ["b"], "n": s["n"] + 1},
               "c": lambda s: {"seen": s["seen"] + ["c"]}},
        edges={"a": "b", "b": "c"},                    # c has no edge: the graph ends
        state={"seen": [], "n": 41},
        start="a")
    assert trace_state["ok"] and trace_state["trace"] == ["a", "b", "c"]
    assert trace_state["state"]["seen"] == ["a", "b", "c"]
    assert trace_state["state"]["n"] == 42             # b's update survived c's merge

    print("22 graph-eng: chain ok")


def test_route():
    ran = []

    def worker(name):
        return lambda s: (ran.append(name), {})[1]

    r = run_graph(
        nodes={"classify": lambda s: {"route": "math" if s["task"].isdigit() else "prose"},
               "math": worker("math"), "prose": worker("prose")},
        edges={"classify": lambda s: s["route"]},      # a coded edge: routes on state
        state={"task": "2742"},
        start="classify")
    assert r["ok"] and r["trace"] == ["classify", "math"]
    assert ran == ["math"]                             # the prose branch never ran

    print("22 graph-eng: route ok")


def test_cycle_budget():
    r = run_graph(
        nodes={"work": lambda s: {"output": "wrong"},
               "check": lambda s: {"passed": False}},
        edges={"work": "check",
               "check": lambda s: END if s["passed"] else "work"},   # the cycle
        state={},
        start="work", budget=5)
    assert r["ok"] is False                            # stopped at the ceiling
    assert r["trace"] == ["work", "check", "work", "check", "work"]

    print("22 graph-eng: cycle-budget ok")


class _Text:                                           # a stand-in Anthropic text block
    type = "text"

    def __init__(self, text):
        self.text = text


class _Msg:                                            # a stand-in Anthropic Message
    def __init__(self, text):
        self.content = [_Text(text)]
        self.stop_reason = "end_turn"


def test_agent_node():
    seen = []

    def model(messages, registry, system):
        seen.append(list(messages))
        return _Msg("42")

    class _Reg:
        def schemas(self):
            return []

    node = agent_node(lambda s: s["task"], model, _Reg(), key="answer")
    updates = node({"task": "add 27 and 15"})
    assert updates == {"answer": "42"}
    assert len(seen[0]) == 1                           # a fresh messages[] per visit
    assert seen[0][0]["content"] == "add 27 and 15"    # the node sees only its prompt

    print("22 graph-eng: agent-node ok")


# (probability, confidence, label, expected verdict). Recorded, not measured.
CASES = [
    ("list the files",        0.01, 0.95, "safe",      "allow"),
    ("read the readme",       0.03, 0.91, "safe",      "allow"),
    ("run the test suite",    0.08, 0.88, "safe",      "allow"),
    ("rewrite git history",   0.44, 0.71, "unclear",   "ask"),
    ("delete a stale branch", 0.55, 0.63, "unclear",   "ask"),
    ("publish a release",     0.97, 0.22, "dangerous", "ask"),      # too flat to act on
    ("drop the database",     0.98, 0.96, "dangerous", "deny"),
    ("force push to main",    0.93, 0.90, "dangerous", "deny"),
]


def test_band():
    got = [route(p, c) for _, p, c, _, _ in CASES]
    assert got == [want for *_, want in CASES]

    allowed = [name for (name, p, c, _, _) in CASES if route(p, c) == "allow"]
    dangerous = {name for (name, _, _, label, _) in CASES if label == "dangerous"}
    assert not (set(allowed) & dangerous)          # one sided: false denies are budgeted

    assert route(None, None) == "ask"              # no layer: the harness decides

    print("22 graph-eng: band ok")


def test_monotonic():
    rank = {"allow": 0, "ask": 1, "deny": 2}
    seen = [rank[route(p / 100, 0.9)] for p in range(101)]
    assert seen == sorted(seen)                    # more risk never loosens the verdict

    print("22 graph-eng: monotonic ok")


def test_decision_edge():
    ask = fixture_asker({name: (p, c) for (name, p, c, _, _) in CASES})
    edge = decision_edge(ask, on={"allow": lambda s: s["route"],
                                  "ask": "escalate", "deny": END})

    assert edge({"task": "list the files", "route": "prose"}) == "prose"
    assert edge({"task": "drop the database", "route": "prose"}) == END
    assert edge({"task": "unrecorded task", "route": "prose"}) == "escalate"

    traffic = [c for c in CASES if c[3] == "safe"]                 # not the attack fixture
    stopped = sum(1 for (_, p, c, _, _) in traffic if route(p, c) != "allow")
    assert stopped / len(traffic) <= 0.25          # the interruption budget

    print("22 graph-eng: decision-edge ok")


if __name__ == "__main__":
    test_chain()
    test_route()
    test_cycle_budget()
    test_agent_node()
    test_band()
    test_monotonic()
    test_decision_edge()
