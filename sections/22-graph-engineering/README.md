# 22 · Graph engineering

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Stop asking the model what runs next. Encode the route you already know, and spend the model only where judgment is needed.

Section 21 stacked loops around one agent. This section shapes the work between model calls.

Many tasks have structure you know before any model call: classify the ticket before acting, review the diff before committing, get approval before anything external.
A plain agent loop rediscovers that structure every run by asking the model what to do next. Model-driven routing is slow, costs tokens, and varies run to run.

Graph engineering encodes the known structure as a directed graph in code:

1. Nodes do the work. A node can be plain code, one model call, or a full agent run.
2. Edges pick the next node. The harness evaluates them in code, not with a model call.
3. Cycles are allowed. Retries, revision after review, and human pauses all need a path backward.
4. State is one record that moves through the graph. Each node reads it and writes its updates.

Code holds the structure. The model supplies the judgment. A loop (section 21) is the smallest such graph: two nodes and one backward edge. This section generalizes it.

---

## Mechanism

![Mechanism diagram](assets/22-graph-engineering.png)

The simple version has three pieces: a dict mapping each node name to a function, a dict mapping each node to what runs next, and one state dict that every node reads and writes.

```python
def run_graph(nodes, edges, state, start, budget=20):  # src/graph.py
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
```

- `nodes` is a dispatch map (section 2). A node reads the state and returns only the keys it changed.
- An edge is a fixed name (deterministic) or a callable on the state (conditional). Either way the harness evaluates it in code. Routing costs no tokens.
- A node with no edge ends the graph. The budget is the section 21 ceiling: a cycle stops at the count and escalates with `ok: False`.
- `trace` records which nodes ran, in order. It is the run's record for section 20.

### Nodes: the determinism-to-agency scale

Each node picks a point on a scale:

- **Code node.** Parse, validate, a fixed API call. Deterministic, no tokens.
- **Model node.** One LLM call, such as a classifier. Bounded judgment.
- **Agent node.** A full section 1 loop with tools. Open-ended judgment inside a fixed slot.

`agent_node` mounts the inner loop as a node. Each visit runs `run_turn` on a fresh `messages[]` built from the state,
so the node sees only what its prompt builder passes it, not the whole run.

The scale is the budget discipline: route with code where the branch is knowable, and spend model calls only inside nodes that need judgment.

### Edges: a typed decision instead of a coded rule

Some branch conditions are hard to write as code. Is this command destructive? Is this ticket about billing?
The harness still controls the branch. Asking the model to choose it would require another full turn.

A third kind of edge asks one narrow question. The call returns a probability, and code uses it to choose the branch:

```python
def route(p, conf, allow_below=0.10, deny_above=0.90, conf_floor=0.45):  # src/decide.py
    if p is None:
        return "ask"                               # no layer, or the call failed
    if conf is not None and conf < conf_floor:
        return "ask"                               # too flat to act on either way
    if p >= deny_above:
        return "deny"
    if p <= allow_below:
        return "allow"
    return "ask"                                   # the band: this is where a person goes
```

`route` still chooses the branch in code, as point 2 at the top of this section requires.
The call supplies a probability that the code cannot compute on its own.

- **Two thresholds, not one.** A single cutoff forces a verdict even when the answer is uncertain.
  Two thresholds leave an abstain band between them. In that band, the harness stops and asks a person.
- **The layer only narrows.** It scores an existing branch. It never adds or grants one.
  A missing key, timeout, or malformed answer returns no number. That routes to `ask`, the harness default when a check fails.
- **Confidence may only tighten.** Low confidence changes the verdict to `ask`. It can never change it to `allow`.

TypeSafe's Jev accepts one state and a map of typed questions.
For each choice question, it returns a probability per option and a confidence value that measures how concentrated the probabilities are.
It answers all questions in the request in one pass, so asking five things costs about what asking one costs.
The response contains no generated prose or chain of thought for the harness to parse.
Nothing is generated, so the call stays small enough to sit on an edge the harness crosses on every step. A second model call would not.

The vendor documents three limits. A typed answer is well formed, but it can still be wrong.
Calibration describes a group of answers. It does not establish whether this answer is correct.
The model reads state as data without treating it as hostile input. Text written to steer the answer can therefore change it.

Use a rule when you can code the branch condition. Use a typed call when the condition needs judgment. Ask a person when the result is in the abstain band.
Section 3's permission rules still apply. The typed check can only restrict what those rules permit.

### Named shapes

The workflow patterns the sources name are graph shapes:

- **Prompt chaining.** A path of nodes, with code gates between them.
- **Routing.** One conditional edge fanning out to specialist nodes.
- **Parallelization.** Sibling branches that run at once and merge at one node, either splitting the task (sectioning) or repeating it for votes (voting).
- **Orchestrator-workers.** A node that decides the fan-out at runtime, then a merge node. The edge set is dynamic; the shape is still a graph.
- **Evaluator-optimizer.** A worker node and a checker node with one backward edge. This is section 21's verification loop as a subgraph.

The names are not settled across sources. `ai-agent-book` covers the same ground under "collaboration topology" and "orchestration",
and mentions "graph engineering" only in a note on terms. This section keeps its own name, because what it describes is a graph written in code.
When you read a second source, match it by mechanism, not by the word.

### When not to graph

Open-ended work resists a predetermined path. Deep research and hard debugging need plans that emerge at runtime; a graph drawn in advance forbids the path the solution needs.
The rule from the sources: encode only structure you would enforce anyway (classify before act, review before commit, approve before send),
and add structure only when it demonstrably improves outcomes. For everything else, use the plain loop and let the model plan.

The hybrid is the common case: an agent as one node inside a fixed graph. The graph guarantees the review happens; the agent decides how to work inside its slot.

### How it integrates

This section adds one small primitive (the edge map) and reuses the rest:

- A node's work is the section 1 loop; `agent_node` wraps `run_turn` unchanged.
- Coded edges follow section 2's dispatch discipline: a map, not model output.
- The worker and checker split across nodes is section 6; sibling branches isolate in section 15 worktrees.
- The step budget and the escalation contract are section 21.
- The trace feeds section 20's telemetry. Which edges fired tells you which branches are dead.
- A typed edge depends on two thresholds that someone must choose. Section 23 checks those choices against a labelled log.

The runnable wires the demo graph from the diagram above:

```python
nodes = {                                          # src/demo.py
    "classify": lambda s: {"route": "math" if any(c.isdigit() for c in s["task"]) else "prose"},
    "math": agent_node(prompt, model, math_reg),   # a full agent run as one node
    "prose": agent_node(prompt, model, Registry()),
    "check": check_node,                           # section 21's checker, now a node
}
edges = {
    "classify": lambda s: s["route"],              # a coded edge: routing costs no tokens
    "math": "check",
    "prose": "check",
    "check": lambda s: END if s["verdict"]["passed"] else s["route"],   # the cycle
}
```

### Further reading

None of this is in `src/`. It comes from ai-agent-book, and is not confirmed of the systems in the table.

**Phase node.** A phase node runs one job as a series of stages, and every stage shares one `messages[]`.
Explore, implement, and review are stages of a single piece of work, not three separate jobs.
The trajectory carries what one stage found into the next, so no stage reads the task again from the top.

**Tools per phase.** Each phase gets its own system prompt and its own tool set, and the harness swaps both when the phase changes.
The history stays where it is, so nothing gets packed for the next phase. The three phases in the book's write-up:

- **Explore.** Read and search.
- **Implement.** Edit and run.
- **Review.** Read, plus a tool that returns a verdict.

**Gate tools.** The model leaves a phase by calling a gate tool, such as `finish_exploring`.
The harness reads that call as the edge and starts the next phase. The gate is the only exit, so the harness decides when a phase ends, not the model.

**The route.** Explore runs first, then implement, then review. A failed review sends the run back to implement,
which picks up with the review's notes already in the trajectory. In this section's terms that is a path with one backward edge,
the same shape as evaluator-optimizer above.

**Which one to mount.** Use a fresh `messages[]` when the branches are unrelated. Use one trajectory when the nodes are stages of one job.
A fresh `messages[]` keeps each node's window small and its branch independent.
One trajectory keeps every earlier finding in view, and it fills more of the window as the path gets longer. The choice is a context question (section 8).

**What counts as multi-agent.** The book calls this design multi-agent, because the prompt and the tools change at every phase.
This repo calls it one agent whose prompt and tools change. The mechanism is the same under either name, so say which definition you mean when you cite the result.

**One source.** The write-up rests on the book's own experiment. No independent report confirms it.

---

## Per system

How each agent decides what runs next.

| | Claude Code | Hermes Agent | mini-swe-agent |
| --- | --- | --- | --- |
| **Pros** | Coded routing: no tokens, no variance. Finished nodes replay on resume. | No graph to author. Structure fits the task. | The whole graph is auditable at a glance. |
| **Cons** | The graph is a per-run script, not a reusable declared graph. | Routing spends tokens and can vary run to run. | One shape for every task. No branch can specialize. |
| **Why** | Orchestration is a program: write once, run deterministically. | Assistant work is too open ended to pre-declare. | Keep choices in the model; a one-cycle harness. |
| **How: nodes** | A subagent per node with schema-checked structured output. | Delegated subagents, capped in depth and concurrency. | Two: a model step and an environment step. |
| **How: routing** | Plain code between stages: conditionals, loops, fan-out. | The model routes by tool call. No coded edges. | One fixed cycle until submit or a budget stop. |
| **How: state** | Return values thread forward; a journal records node output for resume. | Results come back through a completion queue. | The message list is the whole state. |

---

## Failure modes

- **Model as router.** Routing sent to the model burns tokens, adds latency, and varies run to run. A misroute at the top misdirects everything after it.
  Mitigation: evaluate transitions in code; reserve model calls for nodes that need judgment.
- **Probability read as proof.** A typed answer can look correct because it is well formed.
  Mitigation: keep the abstain band. Let the layer remove a branch, never grant one.
- **Evidence the agent can write.** A typed edge can read state containing the model's own output. That output can change the gate's answer.
  Mitigation: build the state from harness-owned fields only. Use the layer for routing, not as a security boundary.
- **Thresholds set once.** Thresholds were fitted to one model version and traffic mix. They stay fixed as both change.
  Mitigation: log decisions without acting on them. Refit from that log. Version the question text and thresholds together (section 23).
- **Over-graphing.** A fixed graph on a task that needed exploration forbids the path the solution needs.
  Mitigation: encode only structure you would enforce anyway; leave open-ended work to the plain loop.
- **No failure edge.** A checking node with nowhere to send a FAIL lets bad output flow downstream.
  Mitigation: give every checking node a backward edge with a budget (section 21).
- **Unbounded cycle.** A retry edge with no ceiling loops forever. Mitigation: a harness-enforced step budget; budget spent means escalate.
- **State bloat.** Every node dumps its full output into shared state, and late nodes drown in it.
  Mitigation: strict state boundaries; a node reads the subset it needs and returns only its updates (section 8).
- **Mid-run death.** A long graph dies at node seven and restarts at node one.
  Mitigation: record each node's output; on resume, replay finished nodes from the record (sections 11, 12).
- **Phase that never ends.** The model never calls the gate tool, so the phase keeps working under the same prompt and the same tools. Only the budget stops it.
  Mitigation: make the gate the only exit; give each phase its own step budget; a spent budget moves to the next phase or escalates.
- **Trajectory that carries every phase.** One trajectory grows with each phase. It still holds calls to tools the current phase does not mount, and the model may try them again.
  Mitigation: name the current phase and its tools in the phase prompt; reject a call to an unmounted tool with a clear error; compact finished phases (section 8).

---

## Runnable

[`src/`](src/) carries 21 forward and adds:

- [`graph.py`](src/graph.py): `run_graph` (a dispatch map of nodes, fixed and conditional edges, threaded state, a step budget) and `agent_node`, the inner loop mounted as a node.
- [`decide.py`](src/decide.py): `route` applies two thresholds with an abstain band between them. `decision_edge` connects the verdict to a branch.
  An offline asker returns recorded answers. A live asker uses stdlib http.
- [`test.py`](src/test.py): offline checks cover chain order, state merge, code-only routing, the cycle budget, and a fresh `messages[]` per agent-node visit.
  They also check the three-way band, no allow for inputs labelled dangerous, missing answers routing to `ask`, monotonic verdicts, and an interruption budget.
- [`demo.py`](src/demo.py): one routed run. A code node classifies, a typed edge checks whether to proceed, an agent node answers,
  section 21's checker grades, and a failed verdict cycles back with feedback.
  The gate uses a recorded answer unless `TYPESAFE_API_KEY` is set. Only the Anthropic key is required to run the demo.

The loop is unchanged. The graph decides when it runs.

```bash
python sections/22-graph-engineering/src/test.py         # offline checks, no key
uv run python sections/22-graph-engineering/src/demo.py  # live demo, needs a key
```

---

## Sources

- [LangChain · 3 years of graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph): nodes, edges, cycles, agents as nodes, when not to graph.
- [Anthropic · Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): workflows vs agents and the five workflow shapes.
- [Google · Why we built ADK 2.0](https://developers.googleblog.com/en/why-we-built-adk-20/): routing in code, context isolation between nodes, agents at workflow nodes.
- [Claude Code](https://code.claude.com/docs): the `Workflow` script contract (pipelines, parallel fan-out, structured output, resume).
  From tool schemas and documented behavior, not the source backup.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent): `tools/delegate_tool.py`, `tools/async_delegation.py`, `batch_runner.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/subsystems/workflow.md`, `packages/workflow/tool-workflow/README.md`: a model-written script per run, no persistent graph.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): the run loop and budgets in `agents/default.py`, `run/benchmarks/swebench.py`.
- [TypeSafe Jev docs](https://docs.typesafe.ai/api.md): requests contain one state and a map of typed questions.
  Choice answers contain a probability per option and a confidence value.
  [Limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13) describes adversarial state and context rot.
  [System One concepts](https://docs.typesafe.ai/concepts/how-to-build-with-system-one) defines the model's role:
  it answers software requests rather than acting as an agent. It never chooses its own next action.
  This section cites the documented contract and limits. The vendor's latency and price results have not been independently reproduced here.
- [ai-agent-book · chapter 10](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md) (《深入理解 AI Agent》, 李博杰, 多 Agent 协作; the Chinese original is canonical):
  multi-stage role switching on one trajectory: a system prompt and a tool set per phase, phase gates as tool calls, and review routing back to implementation.
  The only evidence is the book's own experiment. The same chapter keeps "collaboration topology" and "orchestration" as its primary terms.
