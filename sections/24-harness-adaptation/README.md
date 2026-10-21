# 24 · Harness adaptation

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> An improver can tune an agent's configuration from the agent's own runs, with no grader in the loop.

Section 21's improvement loop assumes a checker: a rubric, a regression set, a holdout.
Section 0 says the same from the other side: when no machine can check the result, add a checker or keep a person in the path.

Open-ended tasks do not fit either answer. They arrive one at a time, with no answer key. Nothing exists to grade against before the deliverable ships.

[harness²](https://github.com/google-research/harness2) tries a third answer. It is not an agent harness.
It runs no loop of its own. It launches an existing coding agent as the executor and edits that agent's configuration files.

It runs the current configuration, lets separate sessions edit copies, runs each copy on the same task, and keeps what the runs support.
A run shows how a configuration behaves, not whether the answer is right.

The cost is more runs per task, and no guarantee of correctness.

---

## Mechanism

![Mechanism diagram](assets/24-harness-adaptation.png)

The simple version is one step (k = 1):

1. **Reference run.** The executor runs the task under the current configuration.
2. **Propose.** Two editors, each its own session, edit a copy. The invariant editor owns how the agent works: procedure, tool use, scripts, subagents.
   The domain editor owns what a practitioner knows: domain facts and skills. Each editor is told to cite the moment in the run behind every edit.
3. **Check.** Structural checks run on each copy (next subsection).
4. **Probe.** Each candidate runs once on the same task, with the same model and the same environment.
5. **Compose.** One session reads the reference run plus each candidate's diff and probe. It merges supported edits, adopts one candidate, or answers `KEEP_BASE`.
   The composer is told that one probe is weak evidence, and that with several reference runs a defect seen in only one of them is noise.
   Its output passes the same checks, retry, and fallback.
6. **Deliver.** The composed configuration's run is the deliverable.

At k = 1 the step spends four executor runs and three editing sessions (two editors, one composer), plus one reflection session when a library is kept.

The moving parts:

- **Executor.** The frozen agent that does the task. Its loop, tools, and weights never change.
- **Configuration.** The executor's editable files.
- **Improver.** The editors and the composer. Each is a separate session that edits a copy, never the live files.
- **Selector.** A session that picks one of several finals when k > 1.
- **Reflector.** A session that writes what the task taught into the library, or writes nothing.
- **Edit surface.** The files the improver may change.
- **Probe.** One run of a candidate on the same task. It is read as a contrast, never as a score.
- **Library.** A per-domain store of adopted configurations, each with a lesson.

### New: the edit surface and its checks

The configuration has eight components, and each has a home section in this repo:

| Component | Home section |
| --- | --- |
| System prompt | 10 |
| Guardrails (always-on prose rules, not a permission gate) | 10 |
| Memory file | 9 |
| Tool guidance | 2 |
| Subagents | 6 |
| Skills | 7 |
| Scripts | 2 |
| Plugins or hooks | 4 |

- **The surface is data.** Each executor declares its own. One executor has four always-on files and plugins. The other has one smaller always-on file and a hooks file instead.
- **The improver edits a copy.** It works in its own workspace. Only the edited configuration folder comes back.
  The method's own prompts and gates are never copied in, so the improver cannot edit them.
- **Checks run before any probe.** A copy fails when:
  - it holds a file off the surface (the whole copy is rejected, like any invalid copy);
  - its always-on text is over a size cap;
  - a skill has no description, or the always-on text never names it;
  - a script does not compile (scripts are compiled, never run);
  - a skill names a script that does not ship;
  - the hooks file binds an event the executor does not dispatch;
  - it carries grader verdict text (a tripwire).
- **One repair before one check.** For a skill the always-on text never names, harness² first adds a trigger line, when it fits under the cap.
  Only then does that check fail.
- **Some rules are only instructions.** The improver is told to route each change by runtime cost: a skill or a script before always-on text.
  Nothing checks that it did.
- **An invalid copy gets one retry.** The validator's message rides into the retry. A second failure keeps the current configuration, recorded as "fell back".
  A valid copy with no change is recorded separately, as "kept on purpose".

```python
def run_stage(editor, config, attempts=2):            # src/adapt.py
    feedback = None
    for n in range(1, attempts + 1):
        out = editor(dict(config), feedback)
        out = dict(config) if out == KEEP_BASE else out
        try:
            check(out)                                 # one ValueError lists every problem
        except ValueError as e:
            feedback = str(e)                          # the message rides into the retry
            continue
        return {"config": out, "fell_back": False, "kept_base": out == config, "attempts": n, "error": None}
    return {"config": dict(config), "fell_back": True, "kept_base": False, "attempts": attempts, "error": feedback}
```

The surface reaches in-loop control. One executor's editable hooks file can bind `PreToolUse` and `PermissionRequest` (section 4).
So an edit can change how the executor gates its own tool calls. Section 21's gates rule has to cover this case.

### New: width or depth under one budget

The budget is k. Two modes spend it:

- **Parallel.** 2k candidates from the same reference runs, each probed. Then k independent compositions, each probed.
- **Sequential.** k rounds. Each round proposes two candidates against the current checkpoint, probes both, then composes and probes the next checkpoint.
  In sequential mode `KEEP_BASE` keeps the checkpoint, never the original.

Both spend exactly 4k executor runs and 3k editing sessions (editors and composers), with the k reference runs shared.
A stage that fell back still counts and still gets probed, so a failure never shrinks the spend. The pick and the reflection run outside that count.

At k = 1 both modes are the one step, with no pick. At k > 1 a selector picks blind among the k finals only.
In parallel mode the finals are the k compositions. In sequential mode they are every checkpoint, not only the last.

- The selector sees the task, the source files, and each final's deliverable under a shuffled id. It sees no configuration and no label.
- It sees a run log only when the deliverable is state with no file to show.
- It picks an existing output. A pick is adoption, not proof.

No rule chooses the mode. Mode and k are fixed before the run.

### New: learning across tasks

After a task, a reflection session reads every candidate, its diff and probe, the decision notes, and which final was picked.
It writes one entry: the whole adopted configuration plus a lesson. The lesson has a cue, the lesson itself, evidence, and a confidence.
It also gives each edit a verdict: helped, hurt, no observed effect, or confounded.

- **Adoption is not evidence.** The pick compared outputs with configurations hidden. It says which output read best, not which edit worked.
- **The entry is gated.** `NO_ENTRY` is a valid answer. An entry missing its cue, lesson, or evidence is never written. A failed pick skips reflection and stores nothing.
- **One library per domain.** Tasks in a domain run in order and read only entries from earlier tasks.
  An index lists each entry's components and cue. The improver opens what looks relevant (section 9's index-first recall).
- **Reuse means copy and probe again.** Every task starts from the base configuration. A stored component reaches a new task only when an editor copies it
  into a candidate, which is checked and probed like any edit. A fact from an earlier task is a hypothesis here.
- **One library per arm.** Each benchmark, executor, k, seed, and mode gets its own library.

### How it integrates

- Section 21's "Online run, offline learner" holds without a rewrite. Executor runs never edit. The improver is a separate session that reads several runs and edits a copy.
  The adopted copy lives for one task, and the next task starts from base. Cross-task carry goes only through the library.
- Stated plainly: no holdout set, no regression set. The gate is a structural check plus one probe on the same task.
- Section 23: no session in the method is handed a rubric, score, or verifier output. A rubric the executor downloads over an open network
  still reaches the editors through the run log (section 23's rubric failure mode). A judge outside the method scores the picked run after the pick.
  It may score reference runs only as a separate baseline, and nothing it writes flows back.
- Section 3: the executor's sandbox and network policy sit outside the configuration.

harness² names these parts its own way. This repo keeps its own terms:

| harness² term | Term in this repo |
| --- | --- |
| harnessing | improvement loop (section 21) |
| execution harness | configuration |
| improvement harness | the improver's fixed prompts plus its library |
| task-level recursion | adaptation inside one task |
| harnessing-level recursion | the library update between tasks |
| Propose, Probe, Compose | one step: propose, probe, compose or keep |
| substrate | executor |
| rollout | reference run or probe |
| harness improvement library | library |

---

## Per system

How each system changes an agent's configuration from past sessions. None of them probes an edit on a task before it lands.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | A note helps from the next session, at no extra run. | Learns from busy sessions with no setup. | Running sessions never change underneath. |
| **Cons** | No note is tested before later sessions load it. | No edit runs before later sessions load it. | Nothing runs the new preset on a task. |
| **Why** | User corrections and confirmations are the evidence to keep. | A model can judge a lesson from the transcript. | An agent is a file, so an agent can write one. |
| **How: who edits** | The agent, plus flag-gated background forks. | The live agent, a reviewer fork, a weekly curator. | An authoring agent, when a person asks. |
| **How: evidence** | User corrections and confirmations. No probe, no score. | Transcripts and usage counts. No probe. | None. A person tries the preset. |
| **How: edit surface** | Memory notes for forks. Any file for the agent. | Skills and memory. Forks never write pinned skills. | One preset folder. Most limits are prose. |
| **How: checks** | None on notes. Settings, skill, and subagent edits need approval. | Format, size, and path checks. | A real mount: every row resolves and starts. |
| **How: carry over** | The note index loads in every later session of that repo. | One store that every later session loads. | Presets picked by id. New ones start as copies. |
| **How: undo** | No versions. A person edits the notes. | A skills snapshot before each curator run. Memory has none. | Running sessions keep theirs. Undo is another edit. |

---

## Failure modes

- **Candidates share a wrong premise.** Every editor reads the same runs with the same model, so all of them can accept one false assumption.
  Agreement then looks like support. Mitigation: treat agreement as support, not proof, and add a probe that tests the shared assumption.
- **An edit chases one lucky probe.** Each candidate runs once, so one good or bad outcome can be noise. Mitigation: treat a defect seen in only one
  of several reference runs as noise, mark an edit confounded when one probe changed several things, and probe again before reuse.
- **A rejected edit stalls the run.** An invalid configuration with no fallback blocks the task or waits on a person.
  Mitigation: send the validator's message into one retry, then keep the current configuration, and record "fell back" apart from "kept on purpose".
- **A library entry carries a payload.** Entries hold scripts and hooks, and a copied component runs inside the next task's executor.
  Mitigation: treat entries as untrusted content (section 3). The improver is told never to run what it reads, and a copy passes the same checks and probe as a new edit.
  Run each probe in a sandbox with egress blocked (section 3). The harness² release does not supply one.
- **The improver sees the score.** A rubric, score, or verifier output in the evidence turns the test set into a training signal.
  Mitigation: evidence with no answers in it, an allowlist on what gets copied in, a tripwire that refuses verdict text, lessons scanned before storage,
  and grading hosts blocked for the executor.

---

## Runnable

[`src/`](src/) carries 23 forward and adds:

- [`adapt.py`](src/adapt.py): a standalone module, not wired into `loop.py`. A configuration is a dict of path to text, and `SURFACE` holds the allowed paths
  and the always-on cap. `check` raises one `ValueError` listing every problem. `run_stage` retries once with that message, then falls back.
  `step` runs one step at k = 1. `run_parallel` and `run_sequential` return the k finals plus a `Ledger` that holds the spend to 4k runs and 3k sessions.
  `Library` gates `add` on a cue, a lesson, and evidence, keeps an index, and `retain` drops entries from later tasks.
- [`test.py`](src/test.py): offline checks for each `check` rule, a retry that repairs a broken script with the error in its second prompt,
  a fallback after two failures, and a no-change copy recorded as kept, not fallen back. Deterministic editors run one step, and the composer sees
  both diffs and probes. `KEEP_BASE` returns the input, and in sequential mode keeps the current checkpoint.
  They also check 4k runs and 3k sessions for k = 1, 2, 3 in both modes, a duplicate label failing the ledger, a retry adding an attempt but no session,
  and reference runs never among the finals. On the library side, an entry missing its cue, lesson, or evidence writes nothing, `NO_ENTRY` writes nothing,
  and each task sees only entries from earlier tasks.
- [`demo.py`](src/demo.py): one live step at k = 1 on section 23's refund episode. The executor is `run_turn` with the configuration's always-on text
  as its system prompt. Each editor and the composer is one model call. The schedule is fixed code, and every judgment is the model's.
  After the step, section 23's `grade` scores only the delivered run, and prints UNGRADED when the grader fails.

`check` is stricter than harness². A skill the always-on text never names fails the check instead of being repaired.
The demo runs the executor four times, so it knowingly bends the one `run_turn` per demo rule. The step is the mechanism.

The loop is unchanged. Adaptation edits what the loop is given, never the loop.

```bash
python sections/24-harness-adaptation/src/test.py         # offline checks, no key
uv run python sections/24-harness-adaptation/src/demo.py  # live demo, needs a key
```

---

## Sources

- [harness² source](https://github.com/google-research/harness2) at `1cfe254` (Apache-2.0): `harness2/core/tree.py` (edit surface, checks),
  `harness2/core/recursion.py` (one step, retry once, parallel and sequential), `harness2/core/ledger.py` (4k runs, 3k sessions),
  `harness2/core/selection.py` (blind pick, reflection), `harness2/core/library.py` (per-domain library, earlier entries only),
  `harness2/core/schedule.py` (base configuration per task, domain order), `harness2/cli/_common.py` (one library per arm),
  `harness2/core/context.py` (evidence as files, verdict tripwire), `harness2/core/proposer.py` (workspace copy),
  `harness2/core/prompts.py` (default `adapt` pack: editor scopes, composer rules, `KEEP_BASE`, `NO_ENTRY`),
  `harness2/substrate/opencode.py` and `harness2/substrate/codex.py` (per-executor surfaces).
- [harness² paper](https://arxiv.org/abs/2610.14224) v1: the why (no ground truth on open tasks, two levels of improvement, frozen weights).
  Where paper and code differ, this section follows the code. Its JobBench runs used a separate `tts` prompt pack; this section describes the default `adapt` pack.
  Its benchmark numbers are author-reported and not repeated here.
- [Claude Code source backup](https://github.com/yasasbanukaofficial/claude-code) at v2.1.88: `memdir/`, `services/extractMemories/`, `services/autoDream/`,
  `utils/permissions/filesystem.ts`. The background forks sit behind feature flags whose default for public users is unverified.
  Internal-only and post-pin features are left out.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent) at v2026.7.1: `agent/background_review.py`, `tools/skill_manager_tool.py`,
  `tools/memory_tool.py`, `agent/curator.py`, `agent/curator_backup.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`: `packages/preset/agent-presets/`
  (README, `src/mount.ts`, `src/authoring.ts`), and the `cordis` authoring preset under `apps/cli/config/agent-presets/`.
