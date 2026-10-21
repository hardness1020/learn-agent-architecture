"""Section 24 offline checks, no key, no network.

test_check(): each structural rule rejects a copy, and one ValueError lists
every problem at once, so the retry can fix them all.

test_stage(): an editor that breaks a script and then repairs it succeeds on
the second attempt, with the validator's message in its second prompt. One
that always fails returns the input with fell_back. A valid copy with no
change is kept_base, a deliberate answer, not a fallback.

test_step(): one step at k = 1 with deterministic editors. The composer reads
both candidates' diffs and probes and merges them, and the final run is the
merged configuration's. A composer that answers KEEP_BASE delivers the input.

test_budget(): for k = 1, 2, 3 and both modes, the spend is exactly 4k executor
runs and 3k editing sessions, the reference runs never enter the finals, and a
retry adds an attempt, not a session. A duplicate label fails the ledger even
when the counts match. In sequential mode KEEP_BASE keeps the current
checkpoint, never the original configuration.

test_library(): an entry missing its cue, lesson, or evidence is never
written, and NO_ENTRY writes nothing. The index lists each entry's components
and cue. Run in order, task i sees only entries from the tasks before it.

    python sections/24-harness-adaptation/src/test.py
"""
from adapt import KEEP_BASE, NO_ENTRY, Ledger, Library, check, run_parallel, run_sequential, run_stage, step

BASE = {"AGENTS.md": "Read an order before you refund it. Use the verify-refund skill.",
        "skills/verify-refund/SKILL.md": "description: refund one order\nRun scripts/refund_check.py first.",
        "scripts/refund_check.py": "print('ok')\n"}


def problems(config):
    try:
        check(config)
    except ValueError as e:
        return str(e)
    return ""


def test_check():
    assert problems(BASE) == ""

    assert "off the edit surface" in problems({**BASE, "loop.py": "x = 1"})
    assert "off the edit surface" in problems({**BASE, "scripts/../scripts/x.py": "x = 1"})
    assert "cap" in problems({**BASE, "AGENTS.md": BASE["AGENTS.md"] + " pad" * 2000})
    assert "no description" in problems({**BASE, "skills/verify-refund/SKILL.md": "Run scripts/refund_check.py first."})
    assert "never named" in problems({**BASE, "AGENTS.md": "Read an order before you refund it."})
    assert "does not compile" in problems({**BASE, "scripts/refund_check.py": "def broken(:\n"})
    assert "missing script" in problems({k: v for k, v in BASE.items() if not k.startswith("scripts/")})
    assert "no configuration" in problems(None) and "no configuration" in problems({})
    assert "not text" in problems({**BASE, "scripts/refund_check.py": 5})        # a reply's JSON is untrusted

    both = problems({**BASE, "loop.py": "x = 1", "scripts/refund_check.py": "def broken(:\n"})
    assert "off the edit surface" in both and "does not compile" in both   # one error, every problem

    print("24 adaptation: check ok")


def test_stage():
    seen = []

    def repairs(config, feedback):
        seen.append(feedback)
        script = "def broken(:\n" if feedback is None else "print('fixed')\n"
        return {**config, "scripts/refund_check.py": script}

    s = run_stage(repairs, BASE)
    assert s["attempts"] == 2 and not s["fell_back"] and not s["kept_base"]
    assert seen[0] is None and "does not compile" in seen[1]          # the error rides into the retry
    assert s["config"]["scripts/refund_check.py"] == "print('fixed')\n"

    s = run_stage(lambda c, fb: {**c, "loop.py": "x = 1"}, BASE)
    assert s["fell_back"] and s["config"] == BASE and s["attempts"] == 2
    assert "off the edit surface" in s["error"]

    for keep in (lambda c, fb: dict(c), lambda c, fb: KEEP_BASE):
        s = run_stage(keep, BASE)
        assert s["kept_base"] and not s["fell_back"] and s["config"] == BASE and s["attempts"] == 1

    print("24 adaptation: stage ok")


ASK = "Ask for the order number before anything else."
FACT = "A refund goes back to the store balance, never to a card."


def execute(config):
    """A stand-in executor: its behavior depends only on the configuration."""
    rules = config["AGENTS.md"]
    return f"{'asked' if ASK in rules else 'guessed'}, {'balance' if FACT in rules else 'card'}"


def invariant(config, evidence, feedback):
    """Owns how the agent works: adds a procedure step."""
    return {**config, "AGENTS.md": config["AGENTS.md"] + "\n" + ASK}


def domain(config, evidence, feedback):
    """Owns what a practitioner knows: adds a domain fact."""
    return {**config, "AGENTS.md": config["AGENTS.md"] + "\n" + FACT}


def merging(config, evidence, pool, feedback):
    """Keeps every edit whose probe changed the behavior the reference runs showed."""
    added = [line for c in pool if c["probe"] not in evidence
             for line in c["config"]["AGENTS.md"].splitlines() if line not in config["AGENTS.md"]]
    return {**config, "AGENTS.md": "\n".join([config["AGENTS.md"], *added])}


def test_step():
    seen = {}

    def composer(config, evidence, pool, feedback):
        seen.update(evidence=evidence, pool=pool)
        return merging(config, evidence, pool, feedback)

    final = step(BASE, execute, invariant, domain, composer)
    assert seen["evidence"] == ["guessed, card"]                        # the reference run
    assert [c["label"] for c in seen["pool"]] == ["inv1", "dom1"]
    assert ASK in seen["pool"][0]["diff"] and FACT in seen["pool"][1]["diff"]
    assert [c["probe"] for c in seen["pool"]] == ["asked, card", "guessed, balance"]
    assert final["run"] == "asked, balance" and final["label"] == "agg1"

    final = step(BASE, execute, invariant, domain, lambda c, e, p, fb: KEEP_BASE)
    assert final["config"] == BASE and final["kept_base"] and final["run"] == "guessed, card"

    print("24 adaptation: step ok")


def test_budget():
    for mode in (run_parallel, run_sequential):
        for k in (1, 2, 3):
            out = mode(BASE, k, execute, invariant, domain, merging)
            assert (len(out["ledger"].runs), len(out["ledger"].sessions)) == (4 * k, 3 * k)
            assert len(out["finals"]) == k
            assert not any(f["label"].startswith("base") for f in out["finals"])   # reference runs are evidence only

    flaky = {"calls": 0}

    def breaks_once(config, evidence, feedback):
        flaky["calls"] += 1
        return {**config, "loop.py": "x = 1"} if feedback is None and flaky["calls"] == 1 else invariant(config, evidence, feedback)

    led = run_parallel(BASE, 1, execute, breaks_once, domain, merging)["ledger"]
    assert len(led.sessions) == 3 and led.attempts == 4                     # the retry is an attempt, not a session

    dup = Ledger(runs=["base1", "inv1", "inv1", "agg1"], sessions=["inv1", "dom1", "agg1"])
    try:
        dup.close(1)                                                       # 4 runs and 3 sessions, one label twice
        raise AssertionError("a duplicate label passed")
    except ValueError as e:
        assert "duplicate" in str(e)

    rounds = {"n": 0}

    def merges_then_keeps(config, evidence, pool, feedback):
        rounds["n"] += 1
        return merging(config, evidence, pool, feedback) if rounds["n"] == 1 else KEEP_BASE

    c1, c2 = run_sequential(BASE, 2, execute, invariant, domain, merges_then_keeps)["finals"]
    assert c1["config"] != BASE and c2["config"] == c1["config"] and c2["kept_base"]   # the checkpoint, not BASE

    print("24 adaptation: budget ok")


LESSON = {"cue": "a refund request with no order number", "lesson": "ask for the number before reading any order",
          "evidence": "inv1 probe asked first; base run guessed", "confidence": "medium"}


def test_library():
    lib = Library()
    for missing in ("cue", "lesson", "evidence"):
        assert lib.add("t1", BASE, {**LESSON, missing: " "}) is False
    assert lib.add("t1", BASE, NO_ENTRY) is False and lib.entries == []   # nothing to learn is a valid answer

    assert lib.add("t1", BASE, LESSON) is True
    assert lib.index() == ["t1: AGENTS.md, scripts/refund_check.py, skills/verify-refund/SKILL.md"
                           " · a refund request with no order number"]

    lib, seen = Library(), {}
    tasks = ["t1", "t2", "t3"]
    for i, task in enumerate(tasks):
        lib.retain(tasks[:i])                          # before task i: only what earlier tasks wrote
        seen[task] = [e["task"] for e in lib.entries]
        lib.add(task, BASE, LESSON)
    assert seen == {"t1": [], "t2": ["t1"], "t3": ["t1", "t2"]}

    lib.retain(tasks[:1])                              # a rerun of t2 cannot see t2's or t3's entry
    assert [e["task"] for e in lib.entries] == ["t1"]

    print("24 adaptation: library ok")


if __name__ == "__main__":
    test_check()
    test_stage()
    test_step()
    test_budget()
    test_library()
