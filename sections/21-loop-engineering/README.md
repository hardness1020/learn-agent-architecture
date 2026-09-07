# 21 · Loop engineering

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Stop writing the next prompt. Design the loop that runs the agent without you.

Every earlier section adds one mechanism around one model call. This section composes them.

Loop engineering names a shift in where the engineering effort goes.
Instead of prompting an agent turn by turn, you build the outer system that discovers work, runs the agent, checks the output, and decides what happens next.
The human moves from operator to designer.

An outer loop must:

1. Start runs from triggers, not only from a user (section 14).
2. Check output before it counts as done.
3. Stop on a budget, not on hope.
4. Persist state so the next run continues instead of restarting (sections 9, 12).
5. Report what happened, even when nobody was watching (section 20).

Without this layer, a human is the outer loop. They prompt, read, judge, and retry by hand, and the agent stops working the moment they do.

---

## Mechanism

![Mechanism diagram](assets/21-loop-engineering.png)

The simple version: the agent loop, wrapped by three more loops. Each wraps the one inside it, and each answers a different question.

1. **Agent loop** (section 1). Calls tools until the task looks done. Answers: how does one step get done.
2. **Verification loop.** Grades the output against a rubric. A failure feeds back into a retry, up to a budget. Answers: is it actually done.
3. **Event loop.** Cron schedules, webhooks, and channels start runs (section 14, section 19). Answers: when does work start.
4. **Improvement loop.** Traces and evals (section 20) feed changes to the harness config, skills, or model. Answers: does the system get better.
   At its mature end this loop edits the harness itself: mine weaknesses from traces, propose a bounded edit, validate it against a regression set.
   The loop structure becomes a search space, not a hand-designed template.

Data moves outward. A trigger fires and enqueues a prompt. The agent loop produces a candidate. The grader scores it.
A failure appends feedback and retries while budget remains. A pass delivers through the task's channel.
The run's trace lands in telemetry, where the improvement loop reads it.

### New: the verification loop

The one loop earlier sections did not build. The inner loop stops when the model says it is done. The verification loop makes "done" a checked claim:

```python
def verified_run(task, worker, checker, budget=2):    # src/verify.py
    feedback = ""
    attempts = []
    for n in range(1, budget + 1):                    # the ceiling: harness-enforced
        out = worker(task + feedback)                 # the inner loop (section 1)
        verdict = checker(task, out)                  # a separate checker (section 6)
        attempts.append({"attempt": n, "passed": verdict["passed"], "reason": verdict["reason"]})
        if verdict["passed"]:
            return {"ok": True, "output": out, "attempts": attempts}
        feedback = f"\n\nA prior attempt was rejected... Why it failed: {verdict['reason']}"
    return {"ok": False, "output": None, "attempts": attempts}   # budget spent: escalate
```

- The grader is a separate agent with a fresh context (section 6). A worker that grades its own output tends to pass it.
  `agent_checker` builds one: each grade runs the inner loop on a new `messages[]`, with PASS or FAIL as the first word of the verdict.
- The rubric is fixed outside the loop. The model can satisfy it, not rewrite it.
- Feedback is data. The failed verdict rides into the retry as part of the prompt, so attempt two knows what attempt one got wrong.
- `ok: False` is the escalation signal. The record of attempts goes to a human; the loop does not retry forever.

One pass or fail is a thin signal. Split the verdict into three questions, and make each one name its evidence:

- **Outcome.** Did the run leave the right state behind. Evidence: the state itself, checked by code wherever code can check it (section 23).
- **Process.** Did the run follow its rules: allowed tools, required order, no skipped confirmation. Evidence: the tool calls in the trace.
- **Quality.** Is the answer good on the parts no code check can express. Evidence: the rubric, with the failing line named.

The runnable grades the third question only. Outcome and process checks need an environment that logs what happened, which is what section 23 builds.
Splitting them tells you what to fix. Outcome passed and process failed means the run got lucky. Process passed and outcome failed means the rules are wrong.

### Budgets and stop conditions

Every loop needs a ceiling the model cannot talk its way past: an iteration count, a token budget, a wall-clock limit, or a dry counter (stop after K rounds that find nothing new).

The harness enforces the ceiling. Asking the model to please stop is a hint, not a stop condition.
In `verified_run` the ceiling is the `range()` bound: attempt `budget + 1` cannot happen.

### Maturity levels

The loop-engineering sources grade loops by how much they are trusted to do:

- **L1 · Report.** The loop reads and reports. A human acts.
- **L2 · Assisted.** The loop drafts the change. A human approves it.
- **L3 · Unattended.** The loop acts. A human audits after the fact.

The level is a permissions decision (section 3). Promote a loop one level only after its output at the current level has been boringly correct.

### How it integrates

This section adds no new primitive. It is the composition of earlier ones:

- Triggers are section 14 schedules and section 19 channels.
- The worker is the section 1 loop, with section 6 subagents as the maker and checker split.
- Parallel loops isolate in section 15 worktrees.
- State between runs lives in section 9 memory and section 12 task records.
- Reports and traces are section 20. The improvement loop closes section 20's measurements back into harness changes.

The runnable wires it the same way. `run_turn` is byte-identical to section 20; verification wraps it from outside:

```python
def worker(prompt):                                # src/demo.py · the inner loop, unchanged
    return run_turn([{"role": "user", "content": prompt}], model, reg, Session(mode=DEFAULT))

checker = agent_checker(RUBRIC, model)             # a fresh grader agent, no tools
result = verified_run("What is 27 + 15? Use the add tool.", worker, checker, budget=2)
```

What is new is the discipline: grade before done, budget before start, report always.

### Further reading

None of this is in `src/`. It comes from ai-agent-book and published self-improvement research, and is not confirmed of the systems in the table.

**Routing a learned change.** Say a run finds out that the staging database needs a different connection string. Where does that go?
The hard part of the improvement loop is not finding the lesson. It is picking where the lesson lands. There are four places to put it:

- **A knowledge doc.** One fact a run discovered. Cheap to write and cheap to delete. The agent reads it back when a task needs it (section 9).
- **A prompt or a skill.** A behavior that should repeat. It costs context on every turn that loads it (section 7).
- **A program.** A procedure that runs the same way every time. It costs nothing at inference, and you can test it (section 2).
- **The weights.** Last resort. Slow, expensive, and the hardest to undo. Outside this repo's harness thesis.

The rule is to pick the smallest place that can hold the change. Smallest also means easiest to check and easiest to undo.
The connection string is a fact, so it goes in a doc. It does not go in the system prompt.

The second place, a prompt or a skill, gets abused most, so it needs gates of its own.
Write the edit from a failure that happened several times, not from one bad run.
Say when it applies, so it stays quiet on unrelated runs. Then check it twice: on cases near the edit, and on a holdout set you did not write it from.
Ship it to part of the traffic first, and keep the rollback ready.
Karpathy calls this system prompt learning: you edit words instead of weights.
ACE keeps each edit small by revising numbered context items instead of rewriting the whole prompt.

**From tool user to tool creator.** The third place, a program, is the one earlier sections do not build.
A skill hands the model instructions that it still has to read and follow (section 7). A compiled workflow hands the harness a program that runs without the model.
Say the agent has booked the same kind of ticket ten times. Five steps turn that into a program:

1. **Capture.** Record one run that worked: which calls it made, in what order, and the state before and after each one.
2. **Parameterize.** Turn whatever changed between runs into arguments. What stayed the same becomes the program.
3. **Validate on reset.** Replay it in a fresh environment (section 23). Every step gets a check before it runs, a check after it runs, and a final state check.
4. **Replay.** Run the program straight through on the next matching task. No model call, so it is fast, cheap, and the same every time.
5. **Invalidate.** One failed check retires the program. The task goes back to the model, which can capture a new one.

Making a tool is the same lifecycle from the other end. The agent hits something it cannot do, finds a library,
wraps it as a tool, and validates it before the registry accepts it (section 2).
Both moves turn one expensive exploration into a cheap capability you can check.
Both need step five, because the site or API they were built against will change.

**Editing the harness.** Say the loop wants to change harness code, not just a prompt. Then it needs a contract before it gets a patch.
The change contract states four things: which traces failed and how often, the root cause, what the change should improve, and how to undo it.
No contract, no patch. A human reads the contract, and that is what separates a self-editing loop from one nobody can audit.
The code the loop may edit is declared up front. Permissions, budgets, and gates sit outside that region, so the loop cannot reach them (section 3).

Where the loop searches is a ladder. The bottom rung is one rule in the prompt.
Above it: how context gets assembled, then the workflow, then harness code, then the code that proposes changes.
Climb a rung only when the rung below fails. Each rung up widens the search and weakens the check on it.
You can A/B test a prompt rule in a day. Swap the code that proposes changes, and every later change gets proposed differently.

**Online run, offline learner.** Keep the two apart. The online loop runs the task and records what happened.
It does not draw lessons, promote skills, or edit the prompt.
A separate offline loop reads many runs at once, finds the failures that repeat, writes candidate changes, validates them, and releases a version.

The split is what stops one run from rewriting the agent. One lucky path is not a pattern.
A web page that told the agent what to remember is not evidence.
Requiring the same signal across several runs, plus a validation gate, keeps both out of a release.

The split also changes what to measure. Read two numbers, not one:

- **Updating.** Is the loop producing good candidates. How many it proposed, how many passed validation, how many got rolled back.
- **Benefit.** Are the shipped changes helping. Does a change load on the runs it targets, does the agent follow it, does held-out performance move.

Read both. With only the first, a skill that is correct but never loads looks like a failed update, and the loop draws the wrong conclusion about itself.

---

## Per system

How each agent composes its outer loops.

| | Claude Code | Hermes Agent | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- | --- |
| **Pros** | Scripted verify plus hard budgets. | Budgets, plus an improvement loop with rollback. | A hard bill per run. | Outer loops attach as plugins on published events. |
| **Cons** | No closed improvement loop in source. | No built-in grade-and-retry loop. | Only the budget half. | Nothing checks the work; rounds are the only budget. |
| **Why** | The outer loop is a program you script. | Improvement should reach the model. | One run is one graded task. | The loop is itself a plugin, so control attaches to it. |
| **How: verification** | Scripted stages, judge panels. | Maker and checker, plus offline tests. | None. SWE-bench grades offline. | None built in; completion is self-declared. |
| **How: event loop** | Cron, wakeups, remote triggers. | Cron with restricted toolsets. | None. The runner schedules tasks, not time. | Reminders replay from the log as a turn. |
| **How: improvement loop** | Resumable workflows replay from cache. | Runs become training data. | None. Budgets only. | None shipped; the attach points exist. |

---

## Failure modes

- **No stop condition.** A retry loop with no ceiling burns tokens until someone notices the invoice. Mitigation: harness-enforced iteration, token, and time budgets.
- **Self-grading.** The worker passes its own output, so the verification loop verifies nothing. Mitigation: a separate checker agent and a rubric fixed outside the loop.
- **Rubber-stamp rubric.** A grader that always passes is worse than none, because it labels bad output as verified.
  Mitigation: adversarial verify (prompt the checker to refute) and periodic human spot checks.
- **Unattended too early.** A loop gets L3 write access before its L1 reports were ever checked.
  Mitigation: climb the maturity ladder one level at a time, gated by section 3 permissions.
- **Silent drift.** An unattended loop degrades and nobody reads its output. Mitigation: heartbeats, always-delivered reports, and section 20 metrics on pass rate and cost.
- **State amnesia.** Each run rediscovers the same work and redoes it. Mitigation: persist findings to memory or task records (sections 9, 12) and read them at run start.
- **Self-editing harness escapes its gates.** An improvement loop that can modify harness code can modify the code that gates it.
  Mitigation: permissions and budgets live outside anything the loop can edit (section 3).
- **Proxy goal drift.** On open-ended work the rubric only stands in for the real goal. The loop learns to satisfy the rubric instead:
  it reuses familiar code, reads noise as a finding, and keeps only the runs that passed. The score climbs and the real goal slips.
  Mitigation: keep the failed runs in the evidence, refresh the holdout set, and have a human check output against the real goal.
- **Every lesson becomes a prompt edit.** The prompt is the easiest place to write, so everything ends up there. It grows until its own rules disagree.
  Mitigation: pick the place by what the lesson is. A fact goes in a doc, a procedure goes in a program, and the prompt is for behavior that must repeat.
- **A compiled workflow outlives its environment.** The site or the API changed, but the program replays anyway. It writes wrong state faster than a model would.
  Mitigation: check before and after every step of a replay, and retire the program on the first failed check.
- **The online run promotes its own lessons.** An agent that draws lessons mid-run can promote a path that just got lucky, or text an untrusted page planted for it to remember.
  Mitigation: let the online loop record evidence and nothing else. A separate offline pass validates candidates before release.

---

## Runnable

[`src/`](src/) carries 20 forward and adds:

- [`verify.py`](src/verify.py): the verification loop (`verified_run`: grade, feedback retry, budget, escalate) and `agent_checker`, a fresh grader per verdict.
- [`test.py`](src/test.py): offline checks for first-try pass, feedback reaching the retry, the budget ceiling, and the PASS/FAIL verdict contract.
- [`demo.py`](src/demo.py): one live verified run: a worker with the add tool, a separate checker grading a fixed rubric, escalation when the budget is spent.

The loop is unchanged. Verification wraps it from outside.

```bash
python sections/21-loop-engineering/src/test.py         # offline checks, no key
uv run python sections/21-loop-engineering/src/demo.py  # live demo, needs a key
```

---

## Sources

- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/subsystems/core.md`, `packages/workflow/tool-ralph/README.md`, `packages/schedule/schedule/README.md`, `docs/subsystems/goal.md`.
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering): building blocks and readiness levels.
- [LangChain · The art of loop engineering](https://www.langchain.com/blog/the-art-of-loop-engineering): the four stacked loops.
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/): the composed building blocks.
- [MindStudio · What is loop engineering](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows): goal conditions.
- [Lilian Weng · Harness engineering for self-improvement](https://lilianweng.github.io/posts/2026-07-04-harness/): the improvement loop in depth; gates outside the loop.
- [ai-agent-book · chapter 8](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter8.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  the three verifier layers, choosing where a learned change lands and the rule for it, the prompt learning gates, the change contract,
  the meta-optimization ladder, the online and offline split, the evolution metrics split, and the verifiable-loop boundary.
- [PreAct](https://arxiv.org/abs/2606.17929): compiling a trajectory into a parameterized workflow with pre, post, and pre-save checks, then replaying it without the model.
  Its first author shares the book author's name, so read its reported replay speedup (roughly 8.5x to 13x) as single-source.
- [Alita](https://arxiv.org/abs/2505.20286): a capability gap triggers tool creation, validated before the tool enters the library.
- Karpathy · "system prompt learning" (X, 11 May 2025): editing words instead of weights, named as a third learning paradigm.
- [ACE](https://arxiv.org/abs/2510.04618): incremental context items with stable ids instead of full prompt rewrites.
- [Lin et al.](https://arxiv.org/abs/2605.30621): harness updating and harness benefit measured separately, with model swaps used to tell them apart.
- [AHE](https://arxiv.org/abs/2604.25850) and [Self-Harness](https://arxiv.org/abs/2606.09498): change contracts and bounded candidate spaces for a harness that edits itself.
- [Claude Code](https://code.claude.com/docs): `/loop`, `ScheduleWakeup`, `Workflow` schema. From tool schemas and documented behavior, not the source backup.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `agent/iteration_budget.py`, `cron/scheduler.py`, `tools/skill_manager_tool.py`, `hermes_cli/curator.py`, `agent/trajectory.py`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `AgentConfig` and `query()` in `agents/default.py`, `agents/interactive.py`, `run/benchmarks/swebench.py`.
