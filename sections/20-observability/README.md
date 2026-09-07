# 20 · Observability & evaluation

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> You cannot fix what you cannot see, and you cannot grade a run nobody recorded.

An agent runs unattended, takes side effects, and spends money. A model call is a black box: it burns tokens and triggers real actions.

Without instrumentation you cannot answer the basic questions. What did it do. How often did a tool fail. What did this session cost.

This section owns the record. It writes down what each step did and what it cost, and it keeps that record clean enough to store.

Whether a change made quality better or worse is a different job. Section 23 does that job, and it runs on what this section records.

Leave the record out and every cost spike is a surprise. Every bug report is unreproducible. The eval set has nothing real to draw from.

---

## Mechanism

![Mechanism diagram](assets/20-observability.png)

Two separable pipelines that never touch the loop's control flow.

Telemetry runs inline: each step calls a fire-and-forget logger.

Events go to sinks, destinations such as the terminal, a file, or a backend like Datadog.
The logger queues events until a sink attaches, then samples, scrubs sensitive fields, and fans out.

Evaluation runs offline against its own task set (section 23). What this section records is what that task set is built from.

- `emit` never blocks and never raises, so a logging fault cannot stall or crash the loop (section 1).
- Events buffer in a queue until a sink attaches, then drain, so the loop can log before telemetry is ready.
- Sampling drops events by rate; scrubbing keeps only allowlisted fields, so code and paths never leak.
- Cost accumulates per model into one USD total, surfaced live and on exit.

### New: fire-and-forget event logging

`telemetry.py` emits events that queue until a sink attaches, then sample, scrub, and fan out. `emit` never raises:

```python
def emit(self, name, **meta):                          # src/telemetry.py
    if not self.sinks:
        self._queue.append((name, meta))               # buffer until a sink is ready
        return
    self._deliver(name, meta)

def _deliver(self, name, meta):
    if not self.sample(name):                          # dropped by sampling rate
        return
    clean = scrub(meta)                                # allowlist before any backend sees it
    for sink in self.sinks:
        try:
            sink(name, clean)
        except Exception:                              # one bad sink never breaks the loop
            pass
```

- Before any sink attaches, events buffer in `_queue`; `attach` drains them through the same `_deliver` path, so queued events are sampled and scrubbed too.
- `scrub` keeps only `SAFE_FIELDS`, so a value not known safe (code, a file path, a prompt) never reaches a backend.
- A sink that throws is swallowed, so one broken backend cannot stall or crash the loop.

### New: per-model cost and offline eval

Cost accumulates per model into one running USD total:

```python
def add(self, model, input_tokens, output_tokens):    # src/telemetry.py
    i, o = self.by_model.get(model, (0, 0))
    self.by_model[model] = (i + input_tokens, o + output_tokens)
    pi, po = PRICES.get(model, (0.0, 0.0))             # modelCost.ts pricing tiers
    self.cost_usd += input_tokens * pi + output_tokens * po
    return self.cost_usd
```

- `add` looks up per-token pricing and rolls the spend into `cost_usd`, the number surfaced live and on exit.
- That total covers the session. It never says which task spent the money.

`run_eval` here is the smallest eval there is. It replays a fixed task set against a candidate build, counts the passes, and returns a rate.
Section 23 puts an environment, a simulated user, and repeat runs under the same entry point. It also explains why a small drop in that rate is usually noise.

### How it integrates

The demo rides telemetry on the model wrapper. The loop does not change:

```python
def model(messages, registry, system):
    r = client.messages.create(...)
    cost.add(MODEL, r.usage.input_tokens, r.usage.output_tokens)   # cost rollup
    tel.emit("model_call", model=MODEL, tokens=..., cost_usd=...)  # scrubbed event
    return r
run_turn([...goal...], lambda m, r, s: model(m, r, SYSTEM), reg, Session(mode=DEFAULT))   # the one agent call
```

- Telemetry observes from outside: the wrapper emits an event and tracks cost, so `run_turn` and dispatch stay byte-identical to section 13.
- The sink prints each event; the session cost prints at the end; then an offline `run_eval` grades a fixed task set.
- Everything upstream is unchanged. Observability is a side-observer, not a new step in the loop.

### Further reading

None of this is in `src/`. It comes from ai-agent-book and two tracing standards, and is not confirmed of the systems in the table.

**Spans, not flat events.** A span is one piece of work inside a run: a model call, a tool call, a retrieval. A trace is the whole run.
Every span records:

- when it started and how long it took,
- whether it succeeded,
- which span is its parent,
- free-form attributes describing the work.

The parent link is the one that matters. It turns the spans of a run into a tree, so reading the tree from the top shows which step failed,
which step was slow, and what each branch cost.

Flat events cannot do that. An event says a call happened, not which step the call belonged to.
One user request can turn into many model calls, tool calls, and retrievals, some nested inside others, some running at the same time.
Sorting that out by timestamp is guesswork.

Two standards fix the shape of a span, so the backend never has to be guessed at:

- OpenTelemetry defines the span itself: trace id, parent id, timings, status, attributes.
- OpenInference names the LLM work on top of it: prompt, completion, model, token counts, tool call.

Write the instrumentation once against those names, and switching backend becomes a config change, not a rewrite.

Export follows the same rule as `emit`: it stays off the hot path. Spans go into a queue and a background worker sends them in batches,
so a slow collector costs the run nothing. This section's `emit` is the flat version of all this.
Add a trace id and a parent id to the same events and the tree is there.

**Nonlinear cost and per-task caps.** Cost tracks how many tokens the model reads, and every turn resends the whole conversation.
So a tool result that came back on turn two is paid for again on turns three, four, and five.
Anything added to the context is paid for by every turn after it, and the total climbs faster than the number of turns.
Step count alone does not predict it.

Two harness features cut part of the bill, and their savings do not add up:

- Prompt caching (section 10) discounts the prefix that stayed the same.
- Compaction (section 8) drops older turns out of the context.

They overlap: compaction removes the same tokens caching would have discounted.

One session total hides all of this, because it never says which task spent the money.
So track cost per task, and give each task a ceiling. The ceiling stops a run the way the step limit stops a loop that will not finish (section 1).
The book is the only source here and cites nothing external for it, so read this cost model as one author's field account.

**Traces feed the eval set.** The two pipelines meet in one direction: a production trace becomes an eval task. Three steps turn one into the other.

- **Pick.** Keep the runs worth learning from: the ones that errored, the ones a user retried or corrected, and the ones that cost far more than the rest.
  A run that went fine adds nothing.
- **Scrub.** The allowlist that keeps code and paths out of a backend keeps them out of the task file too.
- **Rebuild.** A trace holds the starting state and every tool call, so it supplies both the setup for the task and the result the run should have reached.

Do this continuously and the eval set follows what users actually do. Section 23 grades whatever lands there.

---

## Per system

How each agent emits telemetry, tracks spend, and feeds the eval set.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Rich production visibility, cheap and safe. | Even a crashed run leaves a file. | Nothing extra to instrument: what the model sees is logged. |
| **Cons** | Says what happened, not whether the answer was good. | Almost no production telemetry. | Ships no redaction rules. Delivery can lose or repeat. |
| **Why** | Production must be watched without touching the loop. | A benchmark grades offline, so the full record matters. | The session log is already the record, so export it. |
| **How: telemetry** | Events queue for a sink, then sample and scrub. | One trajectory file per run, saved each step. | Session events mirror out through a redaction pass. |
| **How: cost tracking** | Per-model tokens priced into a session total. | Per-call prices roll into run and global totals. | A replay pass prices the log in tokens, not dollars. |
| **How: eval feed** | Not in source; scrubbed traces become regression cases. | Saved trajectories feed a benchmark runner. | Recorded runs replay with no key, as fixtures. |

---

## Failure modes

- **Telemetry on the hot path.** A logging call that blocks or throws stalls the loop (section 1). A span exporter that waits on the network does the same.
  Mitigation: fire-and-forget with a pre-sink queue, a per-sink killswitch, and batched export from a background worker.
- **Sensitive data leaks into logs.** Code, file paths, or prompts reach a general-access backend, or a task file built from a trace.
  Mitigation: allowlist loggable fields and scrub the rest before fan-out or storage.
- **A flat stream with no parent link.** Nothing says which model call belonged to which step, so a failed run has to be pieced together by timestamp.
  Mitigation: a trace id and a parent span id on every event, under naming conventions the backend already understands.
- **Cost drift goes unnoticed.** A model swap or a runaway loop multiplies spend, and one session total hides the single task that burned it.
  Mitigation: per-model and per-task totals surfaced live and on exit, a per-task cap, and the loop's step ceiling (section 1).
- **The eval set drifts from production.** Offline tasks miss real usage, so the suite passes while users fail (section 23).
  Mitigation: keep filtering scrubbed traces of failed and expensive runs into the task set.

---

## Runnable

[`src/`](src/) carries 19 forward and adds:

- [`telemetry.py`](src/telemetry.py): the event logger (`Telemetry.emit`, queue and drain, `sample`, `scrub`), the per-model `CostTracker`, and the offline `run_eval`.
- [`test.py`](src/test.py): queue-then-drain, sampling, scrub plus sink isolation over a real tool dispatch, per-model cost, and an eval that catches a regressed build.
- [`demo.py`](src/demo.py): one agent turn observed by telemetry on the model wrapper, a live session cost, then an offline eval.

The loop and dispatch do not change. Telemetry observes from outside, and the eval it feeds runs off the hot path (section 23).

```bash
python sections/20-observability/src/test.py         # offline checks, no key
uv run python sections/20-observability/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code analytics](https://github.com/yasasbanukaofficial/claude-code):
  `services/analytics/index.ts` (queue + `logEvent`), `sink.ts`, `datadog.ts`, `firstPartyEventLogger.ts`, `sinkKillswitch.ts`, `shouldSampleEvent`.
- [Claude Code cost and diagnostics](https://github.com/yasasbanukaofficial/claude-code):
  `cost-tracker.ts`, `utils/modelCost.ts`, `costHook.ts` (`formatTotalCost`), `diagnosticTracking.ts`, `upstreamproxy/relay.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/subsystems/telemetry.md`, `docs/subsystems/token-meter.md`, `packages/llm/llm-replay/README.md`, `docs/subsystems/invariants.md`.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter6.md`, Chinese original canonical.
  The span tree, nonlinear agent cost with per-task caps, and production traces recycled into the eval set.
  The cost analysis carries no external citation there, so it is single-source.
- [OpenTelemetry tracing](https://opentelemetry.io/docs/specs/otel/trace/api/): the span itself, its parent link, timings, status, and attributes.
- [OpenInference](https://github.com/Arize-ai/openinference): the semantic conventions that name LLM and tool attributes on a span.
- Evaluation is not present in the Claude Code source, and section 23 owns it here. Held-out task sets and LLM-as-judge remain reconstruction and general practice.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `serialize` and `save` in `agents/default.py`, `GLOBAL_MODEL_STATS` in `models/__init__.py`, `run/benchmarks/swebench.py`, `run/utilities/inspector.py`.
- Framing: [learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code).
