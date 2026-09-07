# 23 · Evaluation

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> A pass rate is only worth what the environment behind it is worth.

Section 20 watches production. It says what happened, never whether it was any good. This section answers the other question: did this change make the agent better.

For a single model call that question is easy. Send a prompt, compare the answer to a reference, count.

An agent breaks every part of that. It runs several turns. It asks the user for information the task never stated. It calls tools that change stored data.
It reaches the same result by different routes. Two runs of the same build on the same task can disagree.

So an agent score needs a test bed, not a list of prompts: state that resets, a stand-in for the user, a protocol that steps the conversation,
and a rubric that grades the world the run left behind.

Skip it and the numbers still arrive. They just do not mean anything. A suite whose tasks leaked into training. A 3 point gap that is only sampling noise.
A build that scores well and refunds an order the customer never named.

---

## Mechanism

![Mechanism diagram](assets/23-evaluation.png)

An evaluation environment has five parts. Four are data, one is control flow.

- **Dataset.** The task records. Each holds a starting state, what the user wants, and how the run is checked.
- **Environment state.** The mutable data a task starts from and ends in: orders, files, a database.
  It must be realistic enough to matter and controlled enough to reset.
- **Tools.** The operations the agent is allowed to run. Keep them atomic (read an order, refund an order),
  not one tool named "solve the customer's problem".
- **Rubric.** How a finished run turns into a score.
- **Interaction protocol.** Who speaks when, and when the episode ends.

Section 20's eval took `(input, grade)` pairs: one string in, one string out, one pass rate. This section keeps that entry point and puts an environment under it.

### New: the environment and its reset

The environment holds the state and owns the only way to change it:

```python
def reset(self):                                       # src/evaluation.py
    self.state = deepcopy(self.initial)                # a fresh copy per episode
    self.calls = []

def call(self, name, **args):
    tool = self.tools.get(name)
    if tool is None:
        self.calls.append((name, False))               # illegal: no such tool
        return f"error: no tool named {name}"
    try:
        out = tool(self.state, **args)
    except Exception as e:                             # illegal: wrong arguments
        self.calls.append((name, False))
        return f"error: {type(e).__name__}: {e}"
    self.calls.append((name, True))
    return str(out)
```

- `reset` deep copies the starting snapshot, so episode two never sees episode one's writes.
  Without it, the second run of a refund task starts with the order already refunded.
- A rejected call returns a message saying why, not a bare failure flag. Recovering from it is part of the behavior under test.
- Every call is logged with whether it was legal. That log is the process metrics: illegal calls and step count, available even when the outcome check passes.

### New: the simulated user and the protocol

Most benchmarks hand the agent the full request in the first message. Real users do not.
They open with "something is wrong with my order" and give the rest when asked.

So the simulated user holds a script and releases one fact per turn. That is what makes asking a graded skill:

```python
def run_episode(env, task, agent, max_turns=8):        # src/evaluation.py
    env.reset()
    user = task["user"]()                              # a fresh simulated user per episode
    transcript, said = [], user()
    for _ in range(max_turns):                         # the ceiling: an episode always terminates
        if said is None:
            break
        reply = agent(said, env)
        transcript.append((said, reply))
        said = user(reply)
    return {"transcript": transcript, "state": env.state, "calls": list(env.calls)}
```

The runnable uses a fixed script so the offline checks stay deterministic. The live version is an LLM given the same script.
It is told to answer in character, to release only what the current step needs, and to invent nothing. Wording varies, the order of disclosure does not.

The stronger version gives the simulated user its own tools over the same state. The agent then has to talk someone else into acting, which is what a support call actually is.
It also means the user can change things the agent has to notice, so the state stops being the agent's private sandbox.

### New: grading one episode

Three checks, in order:

```python
def grade(task, run):                                  # src/evaluation.py
    checks = {name: bool(fn(run["state"])) for name, fn in task["checks"]}       # the outcome
    said = " ".join(reply for _, reply in run["transcript"]).lower()
    told = {s: s.lower() in said for s in task.get("must_say", [])}              # what was communicated
    unsafe = [name for name, fn in task.get("veto", []) if fn(run)]              # zero tolerance
    return {"passed": all(checks.values()) and all(told.values()) and not unsafe, ...}
```

- **Outcome, not path.** The checks read the final state, so any route that reaches it passes. A reference solution is one way to solve the task, not the required way.
- **What was said.** A run that refunds the money and never tells the customer the amount is not finished.
  Checking only the state misses that, and checking only the transcript misses "claimed but never did".
- **Veto.** One safety hit fails the run whatever else scored: refunding an order the customer never named, printing a secret, mailing an outsider.
  No other dimension buys it back.

### Metrics: what k runs are for

One run gives a verdict. Several runs of the same task give the number that matters:

- **Pass@k**: at least one of k runs passed. Answers "can it", the right metric for exploratory tasks.
- **Pass^k**: all k runs passed. Answers "is it reliable", the right metric for a regression gate.
- **Best@k**: the best score of k runs, for open tasks with a graded scale rather than a binary verdict.

The two diverge fast. At a 60 percent single-run success rate, Pass@5 is about 99 percent and Pass^5 is about 8 percent.
Report the wrong one and a coin flip looks like a shipped feature.

Alongside them sit the process metrics the call log already carries: share of legal calls, steps against a known-good baseline, retries, and cost per task.
They are what tells you whether a passing run passed cheaply or by brute force.

### Judging open-ended work

State checks cover tasks with a checkable end state. A written answer has nothing to hash, so a model grades it against a rubric.
How well the grading works comes down to how the rubric is written:

1. **Expert grounded.** It encodes what a domain expert checks, not surface fluency.
2. **Full coverage.** Accuracy, completeness, and safety, with the common failure named explicitly instead of implied.
3. **Weighted, with vetoes.** Criteria split into essential, important, and optional, and a veto item such as fabricated facts zeroes the score.
4. **Self contained.** Each item is checkable without the judge's own opinion. "Cites at least two sources and explains how each supports the conclusion" works.
   "Shows deep understanding" does not.

A judge is a model, so it has model failures. It prefers longer answers. It favors whichever candidate it read first.
A judge from the same family as the agent shares its blind spots, so it forgives exactly the errors the agent makes.

The mitigations are cheap. Use judges from different families. Grade pairs twice with the order swapped.
Calibrate the judge against a human-labeled gold set before trusting it at scale, and send disagreement to a human.

### The dataset decides what the score means

A perfect environment running a bad dataset returns noise. Four rules survive across benchmarks.

- **Verifiable.** The answer or end state can be checked without a human reading it.
- **Tiered.** Easy, medium, and hard tasks separated, so a change that only helps easy tasks cannot hide in the average.
- **Human checked.** Someone confirms the task is solvable and the check is fair.
  Whole benchmark subsets exist because the original tasks were underspecified or graded by unfair tests.
- **Contamination defended.** Public tasks reach the next training set. Defenses: a canary string in every task file, withheld answers,
  tasks collected after the model's cutoff, and parameterized templates that generate fresh instances from one shape.

### Reading a difference

Two builds, 100 tasks, 70 percent against 73 percent. That is not a result.

- **The noise band.** The standard error on a rate over n tasks is about the square root of `p(1-p)/n`.
  At 70 percent on 100 tasks that is roughly 4.6 points, so a 3 point gap sits inside the noise.
- **Repeat.** Sampling and tool timing move a score run to run. Take three to five runs per configuration and report the spread, not one number.
- **Pair.** Both builds ran the same tasks, so compare them task by task and look only where they disagree.
  Pairing removes task difficulty from the comparison, so it needs far fewer samples than two independent rates.
- **Count your hypotheses.** Test six changes at 95 percent confidence and there is about a 26 percent chance one looks significant by luck.
  Tighten the threshold or rerun the winner before believing it.

If the improvement you expect is smaller than the band your suite can resolve, the next task is growing the suite, not tuning the agent.

### From a report to a change

A benchmark report is the input to one decision: what to change next.

1. **Suspect the harness first.** A killed process, a buggy grader, or a task that no longer matches production looks exactly like a worse agent.
   Read failing trajectories before touching the agent.
2. **Find the cluster.** An 88 percent overall rate with three of four related tasks failing is not a general weakness, it is one missing capability.
3. **Change one variable.** Fix the model, the seeds, the task set, and the step limit, then change one thing per round. A round that changes three things explains nothing.
4. **Attribute.** Swap the model with the harness fixed to see how much the model carries. Disable one harness component with the model fixed to see what that component is worth.
   This repo's thesis is that both numbers exist.
5. **Scale the evidence to the decision.** Four tasks can justify a bigger run. They cannot justify a deploy.

Inside a product this becomes standing infrastructure. A master switch disables features to get a bare-model baseline.
Feature flags carry the AB test arms and double as a kill switch.
A snapshot of the fully rendered system prompt per commit lets a prompt edit run the eval suite like any other code change.

For the AB tests, separate the mechanism metric you moved (plan length, prompt size) from the goal metric you care about (task success, session cost).
Keep guardrail metrics that stop the experiment even when the goal metric improves.

### How it integrates

Evaluation reuses the harness rather than adding to it:

- The environment's tool interface is section 2's registry, with the handlers pointed at eval state instead of the real world.
- The agent under test is the section 1 loop, unmodified. Nothing in the loop knows it is being evaluated.
- The judge is section 21's checker: a separate agent, a fresh context, a fixed rubric it can satisfy but not rewrite.
- Section 20 supplies the inputs. Scrubbed production traces become new tasks, and its cost tracker gives cost per task.
- The improvement loop is section 21's outer loop with evidence attached: measure, change one thing, measure again.

---

## Per system

How each system builds the test bed a score comes from.

| | Claude Code | mini-swe-agent | τ²-bench | Verifiers |
| --- | --- | --- | --- | --- |
| **Pros** | Bad work is caught before it lands. | The benchmark runner ships with the agent. | Any correct route passes. | One task set can grade any model or harness. |
| **Cons** | No eval suite in source. | The benchmark's tests are the only rubric. | A second model plays the user, so scores drift. | Heavy setup for a one-off eval. |
| **Why** | Catch bad work inside the run. | A task is one repo bug with tests. | Support work is a conversation. | Eval and training share one environment. |
| **How: environment** | Reconstruction: a scratch copy. | One container per instance: that is the reset. | A domain database and a policy. | A fresh sandboxed runtime per run. |
| **How: task set** | Reconstruction: held-out tasks from scrubbed traces. | A published benchmark split. | Hand written per domain. | A module loaded by id, local or from a hub. |
| **How: scoring** | A reviewer agent and a fixed rubric. | Failing tests pass, passing ones stay. | End state against a gold replay. | Reward functions and a code-running judge. |
| **How: repeats** | Verify passes inside one run. | One run per instance. | k trials, scored as reliability. | Rollouts per task is a flag. |

---

## Failure modes

- **Grading the transcript instead of the world.** An agent that says "refunded" scores the same as one that refunded.
  Mitigation: check the end state, and keep a separate check for what the agent had to communicate.
- **Contaminated tasks.** A public benchmark reaches the next training set, so a high score can be recall rather than capability.
  Mitigation: canary strings in task files, withheld answers, tasks collected after the cutoff, and parameterized templates.
- **Reading noise as a result.** A 3 point gap on 100 tasks, one run each, decides nothing.
  Mitigation: repeat runs, compare paired per task, ignore gaps inside the noise band, and tighten the threshold when testing many changes at once.
- **Blaming the agent for a broken harness.** A starved runner, a buggy grader, or a stale task looks exactly like a quality drop.
  Mitigation: read failing trajectories before changing the agent.
- **A judge that shares the agent's blind spots.** Same-family judges forgive the errors the agent makes, prefer longer answers, and favor whichever candidate came first.
  Mitigation: judges from different families, order swapped and graded twice, calibrated against a human-labeled gold set.
- **Reward hacking.** The agent finds a route to the score that skips the work: keyword stuffing, flattering the judge, refusing hard cases.
  Mitigation: veto items in the rubric, process metrics beside outcome metrics, and periodic human spot checks.
- **A suite that cannot see the change.** A 2 point improvement on 40 tasks is unmeasurable, so every round reads as inconclusive.
  Mitigation: grow the task set before iterating further.
- **State leaking between runs.** No reset, or a shallow one, so one task's writes decide the next task's score.
  Mitigation: a deep copy per episode, and one isolated environment per run (section 15).

---

## Runnable

[`src/`](src/) carries 22 forward and adds:

- [`evaluation.py`](src/evaluation.py): the environment with `reset` and a logged tool interface, a simulated user that releases one fact per turn, the episode protocol,
  grading (state checks, what was said, veto), Pass@k and Pass^k, the binomial noise band, and a paired comparison of two builds.
- [`test.py`](src/test.py): offline checks for reset restoring state, a protocol run where the agent has to ask for the order number,
  a safety veto failing a run whose outcome check passed, Pass@k against Pass^k on a flaky build,
  and a regressed build scoring lower with the paired comparison naming what it broke.
- [`demo.py`](src/demo.py): one graded episode. The model plays the support agent, its tool calls land in the environment,
  and the harness scores the state it left behind.

The loop is unchanged. The environment is what makes the score mean something.

```bash
python sections/23-evaluation/src/test.py         # offline checks, no key
uv run python sections/23-evaluation/src/demo.py  # live demo, needs a key
```

---

## Sources

- [ai-agent-book · chapter 6](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter6.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  the five elements of an evaluation environment, progressive information disclosure, the metric dictionary, the rubric criteria,
  statistical significance, the benchmark-to-improvement loop, and internal eval infrastructure.
- [τ-bench](https://arxiv.org/abs/2406.12045) (Sierra): a user simulated by a language model, success judged by comparing the final database state
  against the annotated goal state, and Pass^k for reliability.
- [τ²-bench](https://arxiv.org/abs/2506.07982) and [its source](https://github.com/sierra-research/tau2-bench): the dual-control environment
  where the simulated user also holds tools, and the reward basis (database state hashed against a gold replay, required phrases,
  optional LLM-judged assertions) whose components multiply.
- [Verifiers](https://github.com/willccbb/verifiers): environments as control flow between agents, task sets loaded by id, rollouts per task,
  resume of missing or errored rollouts, and judge agents that execute code against a run's artifacts.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `run/benchmarks/swebench.py`, one container image per instance,
  per-instance trajectory and prediction records.
- [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified): 500 human-validated instances,
  graded by tests that must newly pass plus tests that must keep passing.
- [GAIA](https://arxiv.org/abs/2311.12983): 466 questions with answers withheld for 300 of them, so the leaderboard cannot be scraped.
- [BIG-bench](https://github.com/google/BIG-bench): the canary string carried in every task file to keep benchmark tasks out of web-scraped training data.
- [Rubrics as Rewards](https://arxiv.org/abs/2507.17746) (Scale AI): checklist rubrics that name required facts, required reasoning steps, and the pitfalls that must be penalized.
- [Claude Code](https://code.claude.com/docs): the reviewer and judge stages in the workflow contract, from tool schemas and documented behavior, not the source backup.
  Evaluation suites are not present in the source, so those cells are marked as reconstruction.
