"""Loop engineering (section 21): the verification loop.

The inner loop (section 1) stops when the model says it is done. verified_run
makes "done" a checked claim: a separate checker grades each candidate against
a rubric, a failed verdict rides into the retry as feedback, and a harness-side
budget caps the attempts. Budget spent means escalate (return the record with
ok=False), not retry forever.

The maker and checker split is section 6: agent_checker grades on a FRESH
messages[] each time, so the worker never grades its own output. The rubric is
fixed outside the loop; the model can satisfy it, not rewrite it.

Mirrors the verification loop named by the loop-engineering sources
(grade-and-retry, maker/checker) and Claude Code's Workflow verify stages
(adversarial verify, judge panel).
"""
from __future__ import annotations

from loop import Session, run_turn
from permissions import DEFAULT
from tools import Registry


def verified_run(task, worker, checker, budget=2):
    """Run worker(prompt) until checker passes it or the budget is spent.

    worker(prompt) -> output text (typically run_turn on the inner loop).
    checker(task, output) -> {"passed": bool, "reason": str}, a separate agent.
    Returns {"ok", "output", "attempts"}; ok=False means escalate to a human.
    The range() is the ceiling. The harness enforces it, not the model."""
    feedback = ""
    attempts = []
    for n in range(1, budget + 1):
        out = worker(task + feedback)                 # the inner loop (section 1)
        verdict = checker(task, out)                  # a separate checker (section 6)
        attempts.append({"attempt": n, "passed": verdict["passed"], "reason": verdict["reason"]})
        if verdict["passed"]:
            return {"ok": True, "output": out, "attempts": attempts}
        feedback = (f"\n\nA prior attempt was rejected by review.\nAttempt:\n{out}\n"
                    f"Why it failed: {verdict['reason']}\nFix that and answer again.")
    return {"ok": False, "output": None, "attempts": attempts}   # budget spent: escalate


def agent_checker(rubric, model, registry=None):
    """A fresh checker agent (section 6): grades against the rubric, never edits.

    Each grade runs the inner loop on a NEW messages[] and a new Session, so
    the checker shares no context with the worker. The verdict contract is the
    first word, PASS or FAIL, then one short reason."""
    registry = registry or Registry()                 # a grader needs no tools by default

    def check(task, output):
        prompt = ("You are a reviewer. Grade the output against the rubric. Do not fix it.\n"
                  f"Rubric: {rubric}\nTask: {task}\nOutput:\n{output}\n"
                  "Reply with PASS or FAIL as the first word, then one short reason.")
        text = run_turn([{"role": "user", "content": prompt}], model, registry,
                        Session(mode=DEFAULT)).strip()
        word, _, reason = text.partition(" ")
        return {"passed": word.upper().startswith("PASS"), "reason": reason.strip() or text}

    return check
