"""Observability & evaluation (section 20): watch it and measure it.

Two separable pipelines, neither of which touches the loop's control flow.

Telemetry runs inline. emit() is fire-and-forget: events queue until a sink
attaches (the loop can log before telemetry is ready), then each event is
sampled, scrubbed of sensitive fields, and fanned out to every sink. It never
blocks and never raises, so a logging fault cannot stall or crash the loop
(section 1).

CostTracker accumulates per-model token usage into a running USD total, the
number surfaced live and on exit.

run_eval() runs off the hot path: replay a fixed task set against a candidate
build and grade each output, so a quiet quality regression is caught before
users hit it. Telemetry says what happened; only eval says whether it was good.

Mirrors Claude Code services/analytics/: index.ts queues logEvent then drains
on attachAnalyticsSink, shouldSampleEvent drops by rate, the
_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS marker guards sensitive fields, and
cost-tracker.ts / modelCost.ts roll per-model token cost into a session total.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Fields safe to send to a general-access backend. Anything else is dropped
# before fan-out, so code, file paths, and prompts never leak (the
# _I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS allowlist).
SAFE_FIELDS = {"event", "tool", "model", "duration_ms", "ok", "tokens", "cost_usd"}

# Per-model price per token (input, output) in USD; illustrative, not current.
PRICES = {
    "claude-opus-4-8":   (15e-6, 75e-6),
    "claude-sonnet-5":   (3e-6, 15e-6),
    "claude-sonnet-4-6": (3e-6, 15e-6),
    "claude-haiku-4-5":  (0.8e-6, 4e-6),
}


def scrub(meta: dict) -> dict:
    """Keep only allowlisted fields (stripProtoFields): a value not known safe
    never reaches a backend."""
    return {k: v for k, v in meta.items() if k in SAFE_FIELDS}


class Telemetry:
    """Inline event logger. Fire-and-forget: emit() never blocks and never
    raises, so a logging fault cannot stall the loop (section 1)."""

    def __init__(self, sample=None):
        self.sinks: list = []
        self._queue: list = []                        # events emitted before any sink attached
        self.sample = sample or (lambda name: True)   # shouldSampleEvent: keep or drop

    def attach(self, sink) -> None:
        """Register a sink and drain the pre-sink buffer through it."""
        self.sinks.append(sink)
        pending, self._queue = self._queue, []
        for name, meta in pending:
            self._deliver(name, meta)

    def emit(self, name: str, **meta) -> None:
        if not self.sinks:
            self._queue.append((name, meta))          # buffer until a sink is ready
            return
        self._deliver(name, meta)

    def _deliver(self, name, meta) -> None:
        if not self.sample(name):                     # dropped by sampling rate
            return
        clean = scrub(meta)                            # allowlist before any backend sees it
        for sink in self.sinks:
            try:
                sink(name, clean)
            except Exception:                          # ponytail: one bad sink never breaks the loop
                pass


@dataclass
class CostTracker:
    """Accumulate per-model token usage into a running USD total (cost-tracker.ts)."""
    by_model: dict = field(default_factory=dict)      # model -> (input_tokens, output_tokens)
    cost_usd: float = 0.0

    def add(self, model: str, input_tokens: int, output_tokens: int) -> float:
        i, o = self.by_model.get(model, (0, 0))
        self.by_model[model] = (i + input_tokens, o + output_tokens)
        pi, po = PRICES.get(model, (0.0, 0.0))         # modelCost.ts pricing tiers
        self.cost_usd += input_tokens * pi + output_tokens * po
        return self.cost_usd


def run_eval(build, tasks) -> dict:
    """Replay a fixed task set against a candidate build and grade each output.

    build(task_input) -> output; each task is (input, grade) where grade(output)
    -> bool. Off the hot path: this is how a quality regression is caught before
    a release ships. Returns the pass rate and the per-task verdicts."""
    verdicts = [bool(grade(build(inp))) for inp, grade in tasks]
    passed = sum(verdicts)
    return {"passed": passed, "total": len(tasks),
            "rate": passed / len(tasks) if tasks else 1.0, "verdicts": verdicts}
