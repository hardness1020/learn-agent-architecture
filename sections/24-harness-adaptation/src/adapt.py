"""Harness adaptation (section 24): tune the executor's configuration from its own runs.

Section 21's improvement loop needs a checker: a rubric, a regression set, a
holdout. An open-ended task has none of these before its deliverable ships.
This module is the other answer: run the configuration, let separate editing
sessions change copies, run each copy once on the same task, and keep what the
runs support. Nothing here grades. A probe shows how a configuration behaves,
not whether the answer is right.

  configuration   dict path -> text, the executor's editable files
  SURFACE         the paths the improver may write, plus the always-on cap
  check           structural checks before any probe, one ValueError for all problems
  run_stage       one editing session: retry once with the error, then fall back
  step            one step at k = 1: reference run, two editors, probes, composer
  run_parallel    width: 2k candidates, then k independent compositions
  run_sequential  depth: k rounds, each composing the next checkpoint
  Ledger          the spend: 4k executor runs and 3k editing sessions
  Library         one per domain; a task reads only entries from earlier tasks

The pick among the k finals and the reflection that writes a library entry are
model sessions this module leaves to the caller (see the README).

Mirrors harness2/core/tree.py, recursion.py, ledger.py, and library.py at 1cfe254.
Standalone like spill.py: loop.py never imports it, the executor is a callable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import unified_diff
from fnmatch import fnmatch

SURFACE = {
    "paths": ("AGENTS.md", "skills/*/SKILL.md", "scripts/*.py"),   # ponytail: 3 of the 8 components, one pattern each
    "always_on": ("AGENTS.md",),                                  # text the executor loads every session
    "cap": 4000,                                                  # characters of always-on text
}


def check(config: dict) -> None:
    """Structural checks, before any probe. Raises one ValueError listing every problem.

    Checks shape, never quality: a copy can pass and still be worse. Scripts
    are compiled, never run, because an edited script is untrusted code."""
    if not isinstance(config, dict) or not config:
        raise ValueError("no configuration came back")
    if any(not isinstance(text, str) for text in config.values()):
        raise ValueError(" | ".join(f"{p}: not text" for p, t in config.items() if not isinstance(t, str)))
    problems = []
    for path in sorted(config):
        if ".." in path.split("/") or not any(fnmatch(path, p) for p in SURFACE["paths"]):
            problems.append(f"{path}: off the edit surface")
    always_on = "\n".join(config.get(p, "") for p in SURFACE["always_on"])
    if len(always_on) > SURFACE["cap"]:
        problems.append(f"always-on text is {len(always_on)} characters, over the {SURFACE['cap']} cap")
    for path, text in sorted(config.items()):
        if fnmatch(path, "skills/*/SKILL.md"):
            name = path.split("/")[1]
            if not re.search(r"^description:\s*\S", text, re.M):
                problems.append(f"{path}: no description")
            if name not in always_on:
                problems.append(f"{path}: skill {name} is never named in always-on text")
            for script in re.findall(r"scripts/[\w.-]+\.py", text):
                if script not in config:
                    problems.append(f"{path}: missing script {script}")
        elif path.endswith(".py"):
            try:
                compile(text, path, "exec")
            except (SyntaxError, ValueError) as e:
                problems.append(f"{path}: does not compile: {e}")
    if problems:
        raise ValueError(" | ".join(problems))


KEEP_BASE = "KEEP_BASE"                               # an editor's deliberate answer: change nothing


def run_stage(editor, config: dict, attempts: int = 2) -> dict:
    """One editing session. editor(config, feedback) returns an edited copy or KEEP_BASE.

    An invalid copy sends the validator's message into one retry. A second
    failure keeps the input and records fell_back, so the task goes on with a
    known-valid configuration instead of waiting on a person. A valid copy
    with no change is kept_base: an answer, not a failure."""
    feedback = None
    for n in range(1, attempts + 1):
        out = editor(dict(config), feedback)
        out = dict(config) if out == KEEP_BASE else out
        try:
            check(out)
        except ValueError as e:
            feedback = str(e)
            continue
        return {"config": out, "fell_back": False, "kept_base": out == config, "attempts": n, "error": None}
    return {"config": dict(config), "fell_back": True, "kept_base": False, "attempts": attempts, "error": feedback}


def diff(before: dict, after: dict) -> str:
    """What a candidate changed, file by file, the way the composer reads it."""
    return "\n".join(line for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)
                     for line in unified_diff(before.get(p, "").splitlines(), after.get(p, "").splitlines(),
                                              p, p, lineterm=""))


@dataclass
class Ledger:
    """The spend, by label. close(k) holds both modes to 4k runs and 3k sessions."""
    runs: list = field(default_factory=list)          # executor runs: reference runs, probes, finals
    sessions: list = field(default_factory=list)      # editing sessions: editors and composers
    attempts: int = 0                                 # model calls; a retry is an attempt, not a session

    def close(self, k: int) -> None:
        for kind, labels in (("run", self.runs), ("session", self.sessions)):
            dupes = sorted({x for x in labels if labels.count(x) > 1})
            if dupes:                                 # first, because a duplicate can satisfy the counts
                raise ValueError(f"duplicate {kind} labels: {dupes}")
        if (len(self.runs), len(self.sessions)) != (4 * k, 3 * k):
            raise ValueError(f"spent {len(self.runs)} runs and {len(self.sessions)} sessions, "
                             f"expected {4 * k} and {3 * k}")


def _run(ledger, label, execute, config):
    ledger.runs.append(label)
    return execute(config)


def _edit(ledger, label, editor, config):
    stage = run_stage(editor, config)                 # a fell-back stage still counts, and still gets probed
    ledger.sessions.append(label)
    ledger.attempts += stage["attempts"]
    return {"label": label, **stage}


def _propose(ledger, n, current, evidence, execute, invariant, domain):
    """Two editors, each its own session, then one probe per candidate on the same task."""
    pool = []
    for name, editor in (("inv", invariant), ("dom", domain)):
        c = _edit(ledger, f"{name}{n}", lambda cfg, fb, e=editor: e(cfg, evidence, fb), current)
        pool.append({**c, "diff": diff(current, c["config"]), "probe": _run(ledger, c["label"], execute, c["config"])})
    return pool


def _compose(ledger, label, current, evidence, pool, execute, composer):
    """One session reads the evidence plus every candidate's diff and probe: merge, adopt one, or KEEP_BASE."""
    c = _edit(ledger, label, lambda cfg, fb: composer(cfg, evidence, pool, fb), current)
    return {**c, "run": _run(ledger, label, execute, c["config"])}


def run_parallel(base, k, execute, invariant, domain, composer) -> dict:
    """Width: 2k candidates from the same k reference runs, then k independent compositions.

    Every stage edits the base, so every fallback is the base. Returns the k
    finals; picking one is a blind selection the caller runs (see the README)."""
    ledger = Ledger()
    evidence = [_run(ledger, f"base{i}", execute, base) for i in range(1, k + 1)]
    pool = [c for i in range(1, k + 1) for c in _propose(ledger, i, base, evidence, execute, invariant, domain)]
    finals = [_compose(ledger, f"agg{i}", base, evidence, pool, execute, composer) for i in range(1, k + 1)]
    ledger.close(k)
    return {"finals": finals, "pool": pool, "ledger": ledger}


def run_sequential(base, k, execute, invariant, domain, composer) -> dict:
    """Depth: k rounds, each proposing two candidates against the current checkpoint.

    Each round probes both, then composes and probes the next checkpoint, whose
    run joins the evidence for the round after. KEEP_BASE and every fallback
    keep the current checkpoint, never the original. Every checkpoint is a
    final, not only the last."""
    ledger = Ledger()
    evidence = [_run(ledger, f"base{i}", execute, base) for i in range(1, k + 1)]
    current, pool, finals = base, [], []
    for t in range(1, k + 1):
        pool += _propose(ledger, t, current, evidence, execute, invariant, domain)
        final = _compose(ledger, f"c{t}", current, evidence, pool, execute, composer)
        finals.append(final)
        evidence = evidence + [final["run"]]
        current = final["config"]
    ledger.close(k)
    return {"finals": finals, "pool": pool, "ledger": ledger}


def step(base, execute, invariant, domain, composer) -> dict:
    """One step: reference run, propose, check, probe, compose. Its final run is the deliverable.

    At k = 1 both modes are this step, with no pick: 4 executor runs, 3 editing sessions."""
    return run_parallel(base, 1, execute, invariant, domain, composer)["finals"][0]


NO_ENTRY = "NO_ENTRY"                                 # the reflector's valid answer: nothing worth keeping


@dataclass
class Library:
    """Adopted configurations plus a lesson, one library per domain and per arm.

    The gate is the only rule: an entry missing its cue, lesson, or evidence
    is never written. Reuse means copy and probe again. A stored component
    reaches a later task only when an editor copies it into a candidate."""
    entries: list = field(default_factory=list)

    def add(self, task: str, config: dict, lesson) -> bool:
        if lesson == NO_ENTRY or not all(str(lesson.get(f, "")).strip() for f in ("cue", "lesson", "evidence")):
            return False
        self.entries.append({"task": task, "config": dict(config), **lesson})
        return True

    def index(self) -> list:
        """One line per entry: its components and its cue. The improver opens what looks relevant."""
        return [f"{e['task']}: {', '.join(sorted(e['config']))} · {e['cue']}" for e in self.entries]

    def retain(self, prefix) -> None:
        """Keep only entries written by the tasks before this one, so no task reads a later task's lesson."""
        self.entries = [e for e in self.entries if e["task"] in prefix]
