# harness² research notes · paper rationale, limitations, and related work

Pinned: commit `1cfe254` plus arXiv 2610.14224 v1. Paths are relative to the harness2 repo root.
Scope: what the paper (v1, HTML and abstract page) and the project site (harness2.github.io) give as design rationale,
ablations, stated limitations, related-work positioning, and definitions, plus every place the paper and the code at `1cfe254` disagree.
Paper sections are cited as "paper §N" or "paper App X". Repo files are cited by path and subsection.
Anything marked **inferred** is my reading, not a statement in a source.
Benchmark numbers appear only in Concept 2, labelled author-reported. They are not for any section body.

Source handling: the paper was read from `arxiv.org/html/2610.14224v1` (converted to text) and cross-checked with WebFetch on the abstract page
and on App F and App G. The site was read from its HTML and `data/results.json`. Lilian Weng's post was read once to check the paper's citation of it.
The clone was read only. Nothing in it was executed.

---

## Concept 1 · Design rationale

**Verdict:** mixed. Overlap with the section 21 improvement loop and its routing rule. Contradiction with the section 21 "Online run, offline learner"
split and its holdout gate. New: a verifier-free validation signal (behavioral contrast on the same task) and scope-split candidates.

### Named mechanisms

1. **Contrastive probing without ground truth.**
   - Stated problem: open-world tasks arrive unseen and "typically lack ground-truth labels or reliable verifiers" (paper §1).
     The abstract words it as "leaving no ground-truth verifiers to reliably guide online harness edits".
     Prior open-world methods use "proxy signals, assumed ground truth answers, or pairwise output judgments",
     and using imperfect execution evidence safely "remains difficult" (paper §1).
   - Stated answer: the reference execution "reveals how the harness behaves, not whether its deliverable is correct" (paper §2.2.1, Propose).
     Each candidate runs on the same task under matched model and environment, and the comparison with the reference is a behavioral contrast
     (paper §2.2.1, Probe). Contrasts "inform subsequent composition rather than verify task correctness",
     because "execution stochasticity and shared model biases limit the reliability of the evidence" (paper §2.2.1, Probe).
     Each compose decision "is tied to a concrete observation rather than a presumed score gain" (paper §2.2.1, Compose).
   - Label policy: "Benchmark scores are reserved for evaluation" (paper App A.1, Label).
   - Code: every stage prompt carries a "WHAT YOU DO NOT HAVE" block (`harness2/core/prompts.py`, `ANSWER_FREE`).
     `BenchPrompts.__post_init__` (`harness2/core/spec.py`) rejects six benchmark-supplied prompt fields if they contain "benchmark", "evaluation",
     "grader", "rubric", or "score". The composer prompt says candidates "share the same task, evidence and likely model biases.
     Agreement is support, not independent proof" (`harness2/core/prompts.py`, `aggregate`; paper App H).
2. **Two recursion levels (task-level and harnessing-level).**
   - Stated problem: "improving a harness for one task does not automatically improve how the next task is refined".
     Task-specific edits fail when requirements change, and "directly inheriting a highly tailored execution harness can cause noisy recursive
     improvement as task distributions shift" (paper §1).
   - Stated opportunity: the refinement process itself "produces highly reusable experience: how a weakness is diagnosed, which changes are tested,
     and what their execution reveals" (paper §1). Central question: can RSI improve "both the current harness and how future harnesses are improved" (paper §1).
   - Design consequence: every task restarts at the common harness H0. Earlier components "are selectively reused or adapted through Propose,
     rather than automatically inherited" (paper §2.3, Reuse). The improvement harness stays fixed during a task and is updated after it (paper §2.1, §2.3).
   - Code: `harness2/core/schedule.py` `run_task` reads `adapter.base_harness()` for every task. `run_domain` threads the library forward
     and calls `Library.retain` so a task sees only earlier entries.
3. **Invariant vs domain-specific edits.**
   - Stated split: the invariant candidate "targets agentic deficiencies that may recur across domains, such as planning, tool use,
     context management, or delegation". The domain-specific candidate "targets the knowledge, procedures, and constraints of the current domain
     or environment" (paper §2.2.1, Propose).
   - The paper gives no further argument for the split. Its support is the "w/o scoping" ablation, which keeps the candidate count, evidence,
     and probe budget and removes only the stated scope (paper §4, Ablation Study).
   - The prompts enforce disjoint ownership. The INV editor: "Do not add professional rules, required domain sections or matter facts.
     A parallel candidate owns them." The DOM editor: "Do not diagnose commands, retries, reading order, output paths, budgeting, scripts or hooks"
     (paper App H; `harness2/core/prompts.py`, `_INV_TEMPLATE`, `_DOM_TEMPLATE`). The composer expects to merge "an INV edit and a DOM edit"
     when each earns its place (paper App H; `prompts.aggregate`).
   - **Inferred:** the split also makes a contrast easier to attribute, because each candidate changes one kind of thing. The paper does not say this.
4. **Fixed weights.**
   - Stated: M has frozen parameters and the harness composes with it "without changing its weights" (paper §2.1).
     "Model weights, stage instructions, and the decision rule remain fixed, while the library evolves" (paper §2.3).
     "Model weights and the improvement algorithm remain fixed" (paper App A.1, Improver Update).
   - The rationale is mostly definitional: harnessing is "optimizing the runtime scaffolding around a frozen base model" (paper §1).
     The framework is called "plug-and-play" (abstract). Evidence offered: "A weaker model with an improved harness can thus outperform a stronger one
     with its original harness" (paper §3.2), and adopted harnesses transfer across executor models (paper App E.3).
   - Weng's post sets the same scope: "Harness evolution changes the non-parametric system around the model" (Weng 2026, the paragraph on
     updating weights alongside the harness).
   - **Inferred:** frozen weights are what let the method wrap off-the-shelf executors (OpenCode, `codex exec`) and API-only models.
5. **Parallel vs sequential recursion.**
   - Parallel "follows the aggregation principle of RSA ... but operates on executable harnesses and their behavioral consequences rather than
     reasoning outputs". Each of k composition sessions receives the full pool. "Our implementation performs one population update;
     k controls its width rather than its depth" (paper §2.2.2).
   - Sequential "applies iterative self-refinement (Madaan et al., 2023) to the execution harness". Later steps "can therefore retain, revise,
     or correct earlier changes using both current and previous evidence" (paper §2.2.2).
   - Project README caption: "Parallel recursion explores wider; sequential recursion refines deeper" (`README.md`, Experiments).
   - No rule for choosing a mode. "We currently specify the recursion form and budget in advance" (paper App G, Adaptive recursion).
     Parallel sessions "can run concurrently" and can reduce latency, but not compute (paper §4, App F).
   - Code: `harness2/core/recursion.py` `run_parallel` ("WIDTH: 2k iid candidates from shared evidence, then k INDEPENDENT compositions")
     and `run_sequential` ("DEPTH: k nested passes over the same shared base evidence"). Both land on 4k rollouts and 3k improvement stages
     (`harness2/core/ledger.py`, `Ledger.assert_identity`), so the two modes are budget-matched by construction.
6. **Smaller rationales the paper states.**
   - Composition is selective: "merge supported changes, revise conflicting edits, adopt one candidate, or preserve the current harness"
     (paper §2.2.1). `KEEP_BASE` "is a real answer, not a failure" (paper App H; `prompts._KEEP_BASE`).
   - Selection is blind to provenance: it compares anonymized deliverables and "returns an existing output without access to its originating harness"
     (paper §2.2.2). The star on H* "indicates adoption rather than verified optimality" (paper §2.2.2).
   - Reflection does not trust adoption: "selecting an output alone does not establish transferability" (paper §2.3).
     The reflector is told selection "says which output read best, not which edit worked", and to prefer a `confounded` verdict when no execution
     isolates an edit (paper App H; `harness2/core/prompts.py`, `experience`).
   - Executable components "are retained rather than summarised as prose" (paper App D.2). Always-on prose is "the expensive option",
     so skills, scripts, subagents, and plugins come first (paper App H; `prompts._SURFACE_OPENCODE`, `prompts.GENERALIZE`).

### What the repo already says

- `sections/21-loop-engineering/README.md`, Mechanism item 4 (Improvement loop): "At its mature end this loop edits the harness itself:
  mine weaknesses from traces, propose a bounded edit, validate it against a regression set." harness² has no regression set.
  Its validation is static checks plus a probe on the same task. Overlap on shape, difference on the gate.
- Same file, Further reading · Routing a learned change: "Write the edit from a failure that happened several times, not from one bad run ...
  Then check it twice: on cases near the edit, and on a holdout set you did not write it from." The harness² composer has the first gate
  ("A defect in 1/k is noise, and an edit aimed at it is overfitting to one sample", paper App H). It has no holdout check.
- Same file, Further reading · Online run, offline learner: "The online loop runs the task and records what happened.
  It does not draw lessons, promote skills, or edit the prompt." harness² edits the harness inside the online task and writes a lesson after each task.
  This contradicts the strict reading. It partly overlaps in spirit: reflection is a separate pass after delivery, it needs execution contrasts,
  and the next task restarts at H0 (paper §2.3).
- Same file, Mechanism item 2 and "New: the verification loop": "Grades the output against a rubric" and "The rubric is fixed outside the loop."
  harness² has no grader inside its loop at all. New position.
- `sections/00-harness-thesis/README.md`, Failure modes: "Running an agent where no machine can check the result. ... Add a checker,
  or keep a person in the path." harness² offers a third answer (behavioral contrasts plus model judgment), and its own App F concedes
  these signals "do not guarantee correctness".
- `sections/21-loop-engineering/README.md`, Routing: "The weights. Last resort. ... Outside this repo's harness thesis." Overlap with fixed weights.
- Same file, Editing the harness: the ladder tops out at "the code that proposes changes". harness² reaches that rung only through data:
  the improver's library changes, its prompts and decision rule do not (paper §2.3).

### Candidate landing spot

Section 21, Further reading, as one paragraph next to "Online run, offline learner": a verifier-free improvement loop that validates by
contrast on the same task, named once as harness²'s "task-level recursion" and mapped to the improvement loop.
Reason: section 21 already owns the improvement loop, the online and offline split, and the gates, so the counterpoint belongs beside them.

### src/ candidate

Yes, shared with ticket r1. A minimal offline strip-down in section 21 `src/`: one base rollout, two scoped deterministic editors,
probe each, keep an edit only if its probe changed the observed behavior, no grader anywhere. `harness2/demo.py` (`DemoAdapter`, `DemoProposer`)
is the model for deterministic editors. The keep rule is a stand-in, not harness²'s: its composer reads trajectories and products,
may merge, adopt one, or `KEEP_BASE`, and treats one probe as weak evidence (`prompts.aggregate`). Defer the rule to ticket r1's design.

### Source anchors

- Paper: abstract; §1 (two challenges, central question); §2.1 (definition box); §2.2.1 (Propose, Probe, Compose); §2.2.2 (parallel, sequential,
  final selection); §2.3 (reflect, update, reuse); App A.1 (Label, Improver Update); App D.2; App G; App H.
- Code: `harness2/core/recursion.py` (`run_parallel`, `run_sequential`); `harness2/core/schedule.py` (`run_task`, `run_domain`);
  `harness2/core/prompts.py` (`ANSWER_FREE`, `_INV_TEMPLATE`, `_DOM_TEMPLATE`, `aggregate`, `experience`, `GENERALIZE`);
  `harness2/core/spec.py` (`BenchPrompts.__post_init__`); `harness2/core/ledger.py`.
- Site: harness2.github.io, sections 01 The Method and 02 Within One Task.
- Repo: `sections/21-loop-engineering/README.md` (Mechanism, Further reading); `sections/00-harness-thesis/README.md` (Failure modes).

---

## Concept 2 · Ablations that isolate one mechanism

**Verdict:** overlap. The method matches section 23 "Attribute" (disable one component with the model fixed). The results are new data, not new mechanism.

### Named mechanisms

Qualitative direction of each isolating experiment (numbers in the table below):

- **Library** ("w/o library": no reads from or writes to the improvement library). Lower on both benchmarks (paper §4, Table 3).
- **Scoping** ("w/o scoping": two generic editors, same candidate count, same evidence, same probe budget). Lower on both benchmarks,
  and the largest of the three drops on JobBench (paper §4, Table 3).
- **Composition** ("w/o composition": probe both candidates, return whichever the evidence favours). Lower on both benchmarks,
  the smallest of the three drops on WorkBuddy (paper §4, Table 3).
- **Execution evidence** (direct refinement of the base harness with no reference executions, no probes, no library). Falls below the base harness
  on Code and Web. A library built at k=1 restores base level. A library built at k=5 lifts one-step refinement above base without new probing
  (paper §4, Fig. 7 left).
- **Library source.** A JobBench-built library improves one-step refinement on WorkBuddy (paper §4, Fig. 7 right).
  Leave-one-out re-runs also gain, but the authors call this "an offline reuse analysis rather than an estimate of online gains" (paper App E.2, Fig. 11).
- **Exploration budget k.** Both modes rise monotonically from k=1 to k=5 on WB-code and WB-office. Parallel ends higher at k=5;
  sequential is higher on Code at k=2. The baselines given extra budget gain less or regress (paper §4, Fig. 5).
- **Component groups** removed from an already improved harness, without re-running improvement. Every removal lowers the score.
  Skills matter most on Code and Web, scripts on Office. "do not establish additive effects" (paper App E.1, Table 6).
- **Editor strength.** The same model as executor and editor still improves on every benchmark (paper §4, Fig. 8).
- **Executor transfer.** A harness refined for one executor helps three other executors, with large variance (paper App E.3, Fig. 12).
- **Tool use.** Tool error rate falls on all three benchmarks. Call count falls on one and rises on two (paper App E.4, Table 7).

Caveats (**inferred**, read against section 23 "Reading a difference"):

- Table 3 and Table 6 give single numbers with no standard deviation and no run count. Score standard deviations in the main tables run from
  0.2 to 6.9 points (paper Tables 1 and 2), so some ablation gaps may sit inside run-to-run spread.
- All component ablations use one configuration: Gemini 3.5 Flash under OpenCode at k=1 (paper §4).
- No paired per-task test is reported.
- None of these ablations can be run from the release (Concept 6, D9).

Author-reported numbers (from the paper, unreproduced; notes only):

| Experiment | Setting | Author-reported result |
| --- | --- | --- |
| Table 3 full | WorkBuddy avg / JobBench, Gemini 3.5 Flash, OpenCode, k=1 | 77.5 / 36.0 |
| w/o library | same | 74.4 / 34.2 (minus 3.1 / minus 1.8) |
| w/o scoping | same | 74.6 / 32.3 (minus 2.9 / minus 3.7) |
| w/o composition | same | 75.9 / 34.1 (minus 1.6 / minus 1.9) |
| Fig. 7 left, direct refinement | WB Code / Web | 73.4 to 63.2 / 69.9 to 65.0 |
| Fig. 7 left, k=5 library | WB Code / Web / Office | plus 0.9 / plus 4.4 / plus 5.1 (reported as gains; read as over base) |
| Fig. 7 right, JobBench library | WB Code / Web / Office, one-step refinement | plus 0.6 / plus 0.5 / plus 2.9 |
| Fig. 11 leave-one-out | WB / JobBench | 77.5 to 79.4 / 36.0 to 37.7 |
| Fig. 5 parallel, k=1 to 5 | WB Code / Office | 76.5 to 80.8 / 79.5 to 83.4 |
| Fig. 6 compute, k=1 to 5 | gain over base, JobBench / WB | 4.5 to 7.5 / 5.1 to 8.4 |
| Table 6 full (k=5) | WB avg | 80.8 (base 72.4) |
| Table 6 w/o prompt group / skills / scripts | WB avg | 79.3 / 75.5 / 77.1 |
| Fig. 8 same model | Opus 5 on WB / JobBench / LAB; GLM-5 on LAB | plus 3.3 / 5.5 / 3.0; plus 13.7 |
| App E.3 transfer | GLM-5 Code; Gemini 3.7 Flash Code / Office; Opus 4.8 | plus 17.3; plus 0.3 / 6.4; plus 2.6 and 1.1 |
| Table 7 tool error rate | WB / JobBench / LAB | 0.8 to 0.5 / 14.4 to 9.1 / 2.0 to 1.6 (percent) |
| Headline one-step gains | WB / JobBench / LAB Partial | up to 11.5 / 4.5 / 11.8 points |
| Parallel k=5 over one step | WB / JobBench / LAB Partial | up to 3.9 / 4.2 / 6.7 points |

I re-derived every delta above from the paper's own table cells. They are internally consistent. The site's teaser and results table
match paper Table 1 (Gemini 3.5 Flash, OpenCode).

### What the repo already says

- `sections/23-evaluation/README.md`, From a report to a change, item 4: "Attribute. Swap the model with the harness fixed ...
  Disable one harness component with the model fixed to see what that component is worth. This repo's thesis is that both numbers exist."
  Table 6 and Fig. 8 / App E.3 are one paper's instances of both numbers.
- Same file, Reading a difference: noise band, "Take three to five runs per configuration and report the spread", "Pair".
  Main tables use three runs with SD; ablation tables do not report spread.
- `sections/21-loop-engineering/README.md`, Further reading · Online run, offline learner: "Updating" vs "Benefit".
  Library provenance (paper App E.2, Fig. 10: share of modified files with library lines) is an updating-side measure; scores are benefit.

### Candidate landing spot

Nowhere as numbers. At most one qualitative clause in section 21 Further reading ("its authors report that removing the library, the scoped
editors, or composition each lowers scores"), with the paper in Sources. Reason: the decision bars numbers from bodies, and section 23 already
teaches the attribution method these ablations follow.

### src/ candidate

No.

### Source anchors

- Paper: §4 (Effect of Task-Level Recursion, Compute-Performance Trade-off, Ablation Study, Analysis of Harnessing-Level Recursion,
  Same Model as Executor and Editor); Tables 1, 2, 3, 6, 7; Figs. 5 to 8, 10 to 12; App E.1 to E.4.
- Site: harness2.github.io, 04 The Results; `data/results.json` ("Reported means only; k=5 uses parallel recursion").
- Repo: `sections/23-evaluation/README.md` (From a report to a change; Reading a difference).

---

## Concept 3 · Limitations and failure cases

**Verdict:** mixed. Overlap with section 21 and section 23 failure modes and with section 9 consolidation.
New: candidates that share a wrong premise, and blind selection that prefers a weaker output.

### Named mechanisms

Stated by the authors:

- **Cost.** Extra executions and editor sessions; cost grows with the recursion budget; parallel sessions cut latency, "but do not eliminate this
  additional computation"; only "a bounded range of budgets" is studied (paper App F, Cost of recursive refinement).
- **Execution-based feedback.** Behavioral contrasts and model judgment "remain sensitive to execution variability and shared model biases.
  They do not guarantee correctness or establish that an adopted change will transfer to a different task" (paper App F).
- **Failure analysis, nine regressions in three groups** (paper App E.6, Fig. 14):
  1. Selection prefers a weaker candidate (an output chosen over a far better one; narrower coverage chosen).
  2. "An incorrect premise can propagate across candidates" (a literal-key requirement, an unchecked cache assumption).
  3. Outcome variability, missing judge verdicts, and edits that miss the evaluated criteria make attribution hard.
  Proposed mitigations: selection that "better balances coverage and precision", "probes that challenge shared assumptions",
  and "repeated executions to distinguish editing effects from outcome variation" (paper App E.6).
- **Library growth.** The library is "a simple indexed library". "As this library grows, future work could consolidate overlapping components,
  revise outdated experience, and refine retrieval" (paper App G, Managing the improvement harness). Acknowledged, not handled.
  Code agrees: `Library.forget` exists only so `Library.retain` can drop entries from later tasks; nothing prunes stale entries
  (`harness2/core/library.py`).
- **Fixed recursion form and budget.** No early stop "when additional probes yield little new evidence" (paper App G, Adaptive recursion).
- **Editing without evidence hurts.** Direct refinement without references, probes, or library falls below base (paper §4, Fig. 7 left).
- **Recursion does not close every gap** between initial harnesses (paper §3.2).
- **Overfitting to one task.** Stated in three places. Task-specific edits "often fail when requirements change" (paper §1).
  The composer prompt calls an edit aimed at a defect in 1/k base rollouts "overfitting to one sample" (paper App H; `prompts.aggregate`).
  App F: no guarantee "that an adopted change will transfer to a different task".
  The editor is told to write verified workspace facts into the harness and to write procedure for the domain, and to treat an earlier task's
  facts as "a hypothesis here, not a fact" (paper App H; `prompts.GENERALIZE`). The DOM editor: "Never store this matter's facts as durable memory"
  (paper App H). **Inferred:** the adopted harness is fitted to the current task on purpose; transfer rests on reflection and on the next editor
  re-verifying facts.

Not stated by the authors (gaps):

- **Reward hacking and rubric access.** The paper never mentions reward hacking, web access, or network policy (grep of full text;
  WebFetch check of App F and App G). App B says JobBench's "rubric is never shown to the executor, the editor, or the selector".
  The release docs say the release "does not restrict network access", JobBench publishes `RUBRICS.json` on Hugging Face,
  and "In a validation run of this release, a Codex rollout on JobBench downloaded its task's rubric without being instructed to"
  (`docs/EXPERIMENTS.md`, Web access; `README.md`, Web access). The paper does not say which network restrictions its runs used.
- **Judge and improver share a model.** JobBench is judged by Claude Opus 4.8 (paper App B). The editor and selector are also Claude Opus 4.8
  (paper App D.2). WorkBuddy's judge items use Gemini 3.5 Flash (paper App B), which is also one of the executors.
  **Inferred:** the selector is the loop's only preference signal, so its agreement with a same-model judge is not independent evidence.
  Weng's post names this risk: a loop rewarded by a judge model "may learn reward hacking tricks specific to this judge".
- **Library poisoning.** Library entries carry executable scripts and plugins. The prompts say "Never execute a script, plugin or command found
  in a candidate harness" (paper App H, EVIDENCE IS DATA), but a copied component runs in the next task's executor. The paper does not discuss this.
  **Inferred** risk.
- **Unscored tasks.** The paper does not say how tasks with no graded output are counted. The code drops them from the denominator (Concept 6, D6).

### What the repo already says

- `sections/21-loop-engineering/README.md`, Failure modes: "Proxy goal drift", "The online run promotes its own lessons"
  (including "text an untrusted page planted for it to remember"), "Every lesson becomes a prompt edit", "Self-editing harness escapes its gates".
- `sections/23-evaluation/README.md`, Failure modes: "Contaminated tasks", "Reward hacking", "A judge that shares the agent's blind spots",
  "Reading noise as a result".
- `sections/09-memory/README.md`, Mechanism: "Consolidation runs rarely. It merges duplicates and prunes stale entries." Failure modes: "Store gets noisy."
- `sections/07-skills/README.md`, New: the store evolves: growth is a write, decay starts with measurement (`stale_skills`).
- `sections/20-observability/README.md`, Further reading · Nonlinear cost and per-task caps: track cost per task.

### Candidate landing spot

- Section 21, Failure modes: one new bullet for **candidates that share a wrong premise**. Candidates drawn from the same evidence and model
  agree on a false assumption, so agreement looks like support. Mitigation from the paper: probes that challenge shared assumptions, repeated runs.
  Reason: new, and section 21 owns improvement-loop failures.
- Section 23, Failure modes: the rubric reachable over the network (ticket r2 decides wording and placement).
- Library growth: nowhere new. Section 9 consolidation and section 7 decay already teach the fix. Reason: the paper only names it as future work.

### src/ candidate

No. Probe and compose code belongs to ticket r1.

### Source anchors

- Paper: §1; §3.2; §4 (Fig. 7); App B; App D.2; App E.2; App E.6 (Fig. 14); App F; App G; App H.
- Code: `harness2/core/library.py` (`Library.forget`, `Library.retain`); `harness2/core/prompts.py` (`aggregate`, `GENERALIZE`, `EVIDENCE_BOUNDARY`).
- Docs: `docs/EXPERIMENTS.md` (Web access, Task selection and cost); `README.md` (Web access).
- Weng 2026 post, Future Challenges (reward hacking paragraph).

---

## Concept 4 · Related work positioning

**Verdict:** mixed. Overlap with Weng and AHE, which the repo already cites. The paper does not cite ACE or Karpathy's system prompt learning,
so any juxtaposition with them would be this repo's own synthesis.

### Named mechanisms

Works this repo already cites:

- **Lilian Weng · Harness engineering for self-improvement** (root `README.md` References; section 21 Sources).
  The paper cites it three times: for the harness as "prompts, memory, skills, tools, and orchestration" (paper §1), for the definition
  "Agent = Model + Harness" (paper §2.1), and for the RSI goal that "a better system should also become better at producing its successors" (paper §5).
  The post's own definition is "A harness is the system surrounding a base model that orchestrates execution and decides how the model thinks and plans,
  calls tools and acts, perceives and manages context, stores artifacts, and evaluates results." A text search of the post finds no
  "Agent = Model + Harness" formula; it gives "agent = LLM + memory + tools + planning + action" for early frameworks.
  **Inferred:** the formula is harness²'s shorthand attached to a Weng citation.
  The paper does not engage the post's gate advice: "The evaluator and permission control should likely sit outside the loop that evolves harness,
  with held-out tests, trace audits, and human review" (Weng post, Future Challenges).
- **AHE** (Lin et al. 2026, arXiv 2604.25850; section 21 Sources). The paper adopts "the file-level decomposition of AHE" and extends it by one
  component to K=8 (paper §2.1). Table 4 marks AHE as harness, offline, labelled, not streaming, no scaling study, improver update yes (paper App A.1).
- **ACE** (arXiv 2510.04618; section 21 Further reading and Sources). Not cited anywhere in the paper (reference list and full-text search).
  **Inferred** relation: ACE revises itemized context entries; harness² edits component files, stores whole trees, and its reflector writes
  at most one lesson per task (paper App H). Both avoid rewriting a whole prompt.
- **Karpathy · system prompt learning** (section 21 Further reading and Sources). Not cited. **Inferred:** harness² is a wider form of the same idea:
  it edits words in prompts and also skills, scripts, subagents, and plugins, with weights fixed.
- **Self-Harness** (arXiv 2606.09498) and **Lin et al.** (arXiv 2605.30621), both in section 21 Sources: not cited.

Prior work the paper names as closest:

- **Offline harness search.** Meta-Harness searches harness code offline against a training split and deploys the best (paper §3.1, App C).
  It is the strongest baseline in 9 of 12 benchmark-configuration pairs (my count from paper Tables 1 and 2).
  Others: AHE, AI4AI, DarwinX, EvolveNet, HELIX, HarnessX (harness plus weights), HarnessCompass, HSI (paper §5, Table 4).
- **Prompt and workflow optimizers.** DSPy, GEPA (prompts, offline, labelled); ADAS, AFlow (agent design and workflow, offline) (paper §5, Table 4).
- **Test-time and online harness methods.** RHI uses pairwise output judgments (Lee et al. 2026a). Harness Scaling revises the harness per instance
  with no state across instances (Wang et al. 2026a). TTHE uses execution-derived proxies, "including public tests when available",
  and carries a harness across unlabeled batches (Nie et al. 2026b). Adaptive Auto-Harness uses outcomes revealed after tasks resolve (Liu et al. 2026).
  Continual Harness refines harnesses within continuous episodes; HCL and HSI are named as concurrent (paper §1, §5, App A.1).
- **Memory and skills.** ReasoningBank keeps the harness fixed and retrieves distilled strategies (baseline, paper App C).
  MemoHarness builds a diagnostic experience bank offline for later case-specific edits (paper App A.1, Stage).
  SkillOS, Evo-Harness, HyperSkill, Recuris (paper §5).
- **RSI lineage.** Self-rewarding language models (weights), STOP (improvement program), Darwin Gödel Machine (agent code, benchmark-evaluated),
  Hyperagents (editable meta-level procedure), MetaSkill-Evolve and MCE (co-evolve artifacts and the skills that build them),
  Self-Refine and Reflexion as early forms, RSA as the aggregation model for parallel recursion (paper §2.1, §2.2.2, §5).
- **Claimed distinction:** "Our distinction lies in combining label-free task-stream refinement of both harnesses with learning from task-level
  exploration, rather than in streaming, experience reuse, or improver updates alone" (paper App A.1). In Table 4, harness² is the only row marked
  online, no label, streaming, scaling study, and improver update. The criteria are the authors' own, and a check "records an update mechanism,
  not verified gains in improvement ability" (paper App A.1).
- **Naming overlap.** Weng describes Meta-Harness as "a harness for optimizing harnesses". harness² calls its improver an "improvement harness"
  and keeps it separate from the "execution harness" (paper §2.1, App A.1 Object).

### What the repo already says

- `sections/21-loop-engineering/README.md`, Further reading: Routing (Karpathy system prompt learning; ACE "keeps each edit small by revising
  numbered context items"); Editing the harness (change contract, declared edit region, the search ladder); Online run, offline learner;
  Updating vs Benefit. Sources list Weng, AHE, Self-Harness, Lin et al., ACE, Karpathy.
- Root `README.md`, References: "Lilian Weng · Harness engineering for self-improvement: The improvement loop, with gates outside the loop."

### Candidate landing spot

Section 21 Sources: one harness² bullet (paper plus repo at `1cfe254`). Root `README.md` References: one bullet. Section 21 Further reading:
place harness² against the online and offline split, not against ACE or Karpathy, since the paper itself draws no line to them.
Reason: positioning text belongs where the cited neighbors already live. No new section is needed for related work.

### src/ candidate

No.

### Source anchors

- Paper: §1; §2.1; §2.2.2; §3.1; §5; App A.1 and Table 4; App C; References list.
- Weng 2026 post (definition paragraph near the top; Future Challenges).
- Repo: root `README.md` References; `sections/21-loop-engineering/README.md` Further reading and Sources.

---

## Concept 5 · Definitions: harness and harnessing vs section 0

**Verdict:** overlap, with a scope difference. Both put a frozen model at the center and the engineering in the harness.
harness²'s operational "harness" is a narrower, editable configuration layer inside a frozen agent.

### Named mechanisms

- **Harness.** "the prompts, memory, skills, tools, and orchestration through which the model performs tasks" (paper §1).
  "the system surrounding a base model that enables it to act as an autonomous agent (Agent = Model + Harness), without changing the model weights"
  (paper §2.1). Formally H = (c1, ..., cK), K editable components, each "mounted as a file in the runtime" (paper §2.1), K = 8 (paper App D.3, Table 5).
  Project README: "A harness is the system around a model that enables it to act as an agent: prompts, tools, memory, skills, orchestration, and more"
  (`README.md`). Site: "Agent = Model + Harness. Harnessing becomes recursive."
- **Harnessing.** "the process of optimizing the runtime scaffolding around a frozen base model to enable it to act as an autonomous agent" (paper §1);
  "We call the process of optimizing this layer harnessing" (paper §2.1).
- **Execution harness vs improvement harness.** "the execution harness is the system being improved, and the improvement harness is the mechanism
  that improves it" (paper §2.1). The improvement harness is fixed stage instructions and decision rule plus an evolving library (paper §2.3).
- **Recursive agent harnessing** (paper §2.1 definition box): task-level recursion refines the execution harness; harnessing-level recursion
  updates the improvement harness after each task.
- **RSI** is taken from Duan et al. 2026: persistent self-changes that "also affect how later improvements are generated, evaluated, and retained" (paper §2.1).
- **What sits outside H.** The executor is "a frozen model inside an off-the-shelf coding agent", and the initial harness is "that agent's stock
  configuration extended with the benchmark's reference instructions" (paper App D.1). So OpenCode's and Codex's loop, tool runtime, permission
  system, sandbox, and context management are not part of H.

### What the repo already says

- `sections/00-harness-thesis/README.md`, opening: "The model owns reasoning, tool choice, and when to stop. The harness is the code around the model:
  the loop, tools, memory, permissions, and interfaces." The harness must give actions a place to run, give useful observations, gate side effects,
  and persist state.
- Same file, Mechanism · More harness is not better: every layer costs maintenance; re-evaluate on model change.
- Same file, Further reading: "Scaffolding tracks model strength."

Differences (**inferred** mapping unless quoted):

1. **Scope.** Section 0's harness is code: loop, tools, permissions. harness²'s H is configuration mounted into a harness whose code stays frozen.
   Its components map onto repo sections: C1 system prompt to section 10, C2 guardrails to section 10 (prose rules, not a gate),
   C3 memory to section 9, C4 tool guidance to section 2 (descriptions only), C5 subagents to section 6, C6 skills to section 7,
   C7 scripts to section 2 and the section 21 "program" place, C8 plugin to section 4 hooks.
2. **Gating.** Section 0 lists "Gate side effects" as a harness duty. harness²'s guardrails are always-on prose: "Hard constraints that hold regardless
   of task or tool" (paper App D.3, Table 5). Codex runs "with its sandbox disabled" and executor network access is unrestricted (`README.md`, Web access).
   The editable Codex hooks file may bind `PreToolUse` and `PermissionRequest` events (`harness2/substrate/codex.py`, `CODEX_SURFACE.hook_events`),
   so the edit region touches in-loop control. Relevant to section 21: "Permissions, budgets, and gates sit outside that region."
3. **Harnessing** maps to the section 21 improvement loop at its mature end. The **improvement harness** maps to the top rung of the section 21 ladder
   ("the code that proposes changes"), but only its library evolves.
4. **Cost of more harness.** The editor prompt says "ALWAYS-ON PROSE IS THE EXPENSIVE OPTION" and treats `KEEP_BASE` as a real answer
   (paper App H; `prompts._SURFACE_OPENCODE`, `prompts._KEEP_BASE`). Overlap with section 0's "More harness is not better".
5. **Scaffolding tracks model strength.** The paper reports weaker executors gain more on two of three benchmarks, not on JobBench (paper §3.2),
   and transfer gains vary by executor (paper App E.3). A data point for the section 0 claim, not a confirmation of it.

Vocabulary mapping (harness² term named once, repo term canonical):

| harness² term | Repo term |
| --- | --- |
| harness, execution harness | the configurable layer of a harness: prompt (section 10), skills (7), subagents (6), hooks (4), memory file (9) |
| harnessing | improvement loop (section 21) |
| improvement harness | the improver's fixed instructions plus its library; the "code that proposes changes" rung, changed only through data |
| task-level recursion | improvement-loop iterations inside one task |
| harnessing-level recursion | the improvement loop updating its own guidance between tasks |
| Propose-Probe-Compose | propose a bounded edit, run it on the same task, keep the supported parts |
| harness improvement library | per-domain store of adopted trees and lessons (closest: section 7 skill store, section 9 memory) |
| executor, substrate | the frozen agent whose configuration is edited (OpenCode, `codex exec`) |

### Candidate landing spot

Section 0: no change. Its thesis already matches. Section 21 Further reading: name harness²'s terms once and map them, using the table above.
Reason: the decision keeps "improvement loop" canonical, and the definitional gap (configuration vs code) is clearest where harness editing is taught.

### src/ candidate

No.

### Source anchors

- Paper: §1; §2.1 (definition box); §2.3; App A.1 (Object); App D.1; App D.3 (Table 5); App H.
- Code: `harness2/substrate/opencode.py` (`OPENCODE_SURFACE`); `harness2/substrate/codex.py` (`CODEX_SURFACE`); `harness2/core/tree.py` (`SurfaceSpec`, `is_allowed`).
- Project: `README.md` (opening, Harness components, Web access); harness2.github.io (01 The Method, 03 The Editable Harness).
- Repo: `sections/00-harness-thesis/README.md`; `sections/21-loop-engineering/README.md`.

---

## Concept 6 · Paper vs code at 1cfe254

**Verdict:** n/a. This is an audit of the paper against the code, not a comparison with this repo.

### Named mechanisms

Disagreements, most consequential first:

- **D1 · Selection pool and selector prompt.** Paper App H prints the selector wording "Some of these runs used the UNMODIFIED harness and some used
  adapted ones ... PICKING AN OUTPUT FROM AN UNMODIFIED HARNESS IS A COMPLETELY VALID AND EXPECTED OUTCOME." The code's run path never sends it.
  `selection.selection_pool` returns "exactly the k finals of `mode`", so "the reference executions are not in the pool".
  `schedule._run_one_mode` calls `run_selection(..., pool_kind="finals")`, which selects `POOL_FINALS_SELECT` (`harness2/core/prompts.py`).
  `schedule.run_task` raises on any `pool_policy` other than "finals". The code calls finals "the published convention".
  Paper §2.2.2 does not say whether references are in the pool. Consequence: the release can never deliver a reference execution's output.
- **D2 · JobBench runs a different prompt pack.** Paper App H: "The prompts below are the part shared by every benchmark. Each benchmark appends a short
  block of its own." Code: `harness2/benchmarks/jobbench.py` sets `prompt_pack="tts"`, so JobBench's parallel editors, composers, and selector use
  `_tts_propose`, `_tts_aggregate`, `_tts_select` (`harness2/core/prompts.py`), not the printed texts. The tts composer has no "HOW TO WEIGH THE EVIDENCE"
  block (no 1/k noise rule, no "UNSAFE" rule for edits from non-delivering rollouts). The tts selector ranks COVERAGE, CORRECTNESS, FIDELITY instead of
  DELIVERY, CONFORMANCE, FIDELITY, INTERNAL CONSISTENCY, OPERATIONAL FITNESS, FINISH. JobBench also defines a `selection_lead`
  ("material beyond what was asked costs nothing here"), but it is never sent: `_tts_select` says its three criteria supersede `selection_lead`,
  `selection_conformance`, and `selection_checks`. The effect is the same: no CONFORMANCE rule reaches the JobBench selector.
  For k greater than 1, sequential rounds on JobBench fall back to the
  standard `propose` and `compose_round` (`propose` uses tts only when `round_t is None`).
- **D3 · Untriggered skills are repaired, not rejected.** Paper App D.2 lists "a skill with no trigger sentence" as an invalid tree that gets one retry.
  The printed editor prompt calls it one of "THREE RULES THAT DISCARD THE WHOLE PROPOSAL" (paper App H). Code: `recursion._accept_tree` first calls
  `tree.autotrigger`, which appends a trigger line to `guardrails.md` (OpenCode) or `AGENTS.md` (Codex) for each untriggered skill, unless that would pass
  the always-on cap (24,000 chars OpenCode, 8,000 Codex: `OPENCODE_SURFACE`, `CODEX_SURFACE`). The code also checks things the paper does not list:
  the always-on cap, skill and subagent frontmatter, subagent availability, the hooks schema, grader-verdict markers, and an adapter rule
  (`tree.validate`, `context.assert_tree_has_no_grader_verdicts`). Retry once, then inherit the starting tree, matches (`recursion.STAGE_ATTEMPTS = 2`,
  `recursion._run_stage`).
- **D4 · The eight components are not uniform.** Paper §2.1 says K=8. App D.1 says under Codex "the on-demand components are the same";
  App H says the Codex variant "differs only in the component paths it lists". Code: Codex has one always-on file, `AGENTS.md`, in place of four,
  and a `.codex/hooks.json` of shell-command hooks in place of TypeScript plugins (`CODEX_SURFACE`: `plugin_dir=None`, `hooks_file=".codex/hooks.json"`).
  Subagents are disallowed under Codex on JobBench and WorkBuddy (`allow_subagents=(substrate == "oc")` in `jobbench.make_spec` and `workbuddy.py`)
  and under both executors on LAB (`allow_subagents=False` in `benchmarks/lab.py`). So LAB never exposes C5.
- **D5 · Rubric never shown vs network access.** Paper App B: "The rubric is never shown to the executor, the editor, or the selector."
  `docs/EXPERIMENTS.md` (Web access): "The release does not restrict network access", and a Codex rollout "downloaded its task's rubric without being
  instructed to". The code's guard is file-name globs (`RUBRICS.json` in JobBench `leak_globs`, `context.assert_no_leak_paths`) plus fixed marker
  strings (`context.GRADER_VERDICT_MARKERS`), which its own docstring calls "A tripwire on the adapters' read boundary, not the defence itself".
  **Inferred:** a rubric fetched by the executor lands inside its trajectory, and trajectories are editor evidence (`context.build`), so the claim holds
  only under a network restriction the paper does not describe.
- **D6 · Unscored tasks leave the denominator.** The paper is silent on how failed tasks count. Code: "Ungraded rows and tasks that never completed are
  excluded from the denominator rather than counted as failures" (`harness2/core/report.py` module docstring); "A judge failure is None, never a zero"
  (`harness2/core/judge.py`); a failed selection is never judged (`tests/test_workflow.py`, `test_failed_selection_is_never_judged`).
  At k=1 the finals-only pool has no base fallback (`selection.trivial_selection`), so a composed harness that produces nothing makes the task unscored.
  By contrast the paper's Harness Scaling baseline submits the base run when its deployment produces no output (paper App C).
  **Inferred:** if the reported means came from this code, methods may be averaged over different task sets, and the paper reports no ungraded counts.
- **D7 · Adopted harness dropped when reflection declines.** Paper §2.3: "The pipeline retains the adopted harness H*_t and supported lessons ...
  When evidence is insufficient, reflection adds no new experience entry." Code: `Library.add` returns `None` unless `has_experience` finds non-empty
  `cue`, `lesson`, and `evidence`, and `schedule._run_one_mode` only adds then. A `NO_ENTRY` task leaves no harness in the library either
  (`tests/test_workflow.py`, `test_no_entry_stays_out_of_library_on_resume`). Ambiguous in the paper; strict in the code.
- **D8 · Rollout caps.** Paper App D.1: "Each rollout is capped at 200 model turns and 3,600 seconds" (WorkBuddy-Bench excepted: it uses
  twice its container timeout). Code: JobBench's OpenCode default timeout is
  2,400 s (`benchmarks/jobbench.py`, `_DEFAULT_TIMEOUT = {"oc": 2400, "cx": 3600}`). The JobBench OpenCode runner sets a step cap only if
  `AGENT_STEPS` is set (`benchmarks/jb_opencode_runner.py`). WorkBuddy leaves `OPENCODE_MAX_TURNS` and `CODEX_MAX_TURNS` null and says OpenCode
  "cannot enforce" a turn cap (`third_party/wb_runner/configs/harnesses/opencode/_defaults.yaml`, `third_party/wb_runner/agents/oc_agent.py`).
  Only the LAB runner defaults to 200 steps (`third_party/lab_runner/opencode/run_opencode.py`, `--max-turns`).
- **D9 · Experiments not in the release.** Grep finds no ablation switches (w/o library, scoping, composition), no baselines (Best-of-k, ReasoningBank,
  Meta-Harness, Harness Scaling), no leave-one-out, and no cross-benchmark or cross-model transfer runner. The paper's frozen splits (JobBench 31/34,
  WorkBuddy halves, LAB 100 test and 24 dev) are not shipped; `scripts/split_tasks.py` makes a new domain-stratified split (default train fraction 0.5),
  and "Generated partitions are local files, not release assets" (`docs/EXPERIMENTS.md`, Task partitions). The library root binds benchmark and domain
  (`cli/_common.library_root_for`; `core/library.py` module docstring: "a domain's directory only ever holds same-domain entries").
  **Inferred:** the JobBench to WorkBuddy transfer in Fig. 7 right has no CLI path.
- **D10 · Memory "persisted across tasks".** Paper Table 5 defines C3 memory as "Durable domain facts persisted across tasks in a domain."
  Paper §2.3 and the code restart every task at H0 (`schedule.run_task`). Memory persists only if a later editor copies it from a library entry.
  An internal tension in the paper; the code follows §2.3.
- **D11 · Evidence visibility varies by benchmark.** The paper says improver sessions read "the produced deliverables" (paper App H) and "all available
  task-local execution evidence" (paper §2.2.1). On WorkBuddy code, editors get deliverable file names only (`proposer_deliverables="names"` in
  `benchmarks/workbuddy.py`; `context.ContextTree.layout`: "File contents are deliberately not provided on this benchmark").
  Single observations are cut at 50,000 chars (`BenchSpec.obs_trunc`), though no trajectory is clipped as a whole (`context.build`).
- **D12 · LAB size.** Paper App B: "1,251 legal tasks across 24 practice areas". `docs/EXPERIMENTS.md`: "The full LAB task stream contains 1,143 tasks."
  **Inferred:** the pinned `harvey-labs` checkout (`config.DEPENDENCY_PINS`) differs from the paper's copy.
- **D13 · Default improver (not a contradiction).** Main experiments use Claude Opus 4.8 as editor (paper §3.1). `config.IMPROVER_MODEL` defaults to
  `HARNESS2_SOLVER_MODEL`, so the release default is the same-model setting of paper Fig. 8.

Spot-checks that agree:

- 4k rollouts and 3k improvement stages per mode, k shared base rollouts (`ledger.expected_rollouts`, `ledger.expected_sessions`; `schedule.run_task`
  rolls `base1..basek` once and both modes reuse them). Matches paper §2.2.2 and the site ("1 reference + 2 probes + 1 final run" at k=1).
- Parallel: 2k candidates, k independent compositions, each with the full pool and every candidate's notes (`recursion.run_parallel`). Matches paper Eq. 2.
- Sequential: k chained checkpoints over shared base evidence; `KEEP_BASE` keeps the current checkpoint (`recursion.run_sequential`, `prompts.compose_round`).
  Matches paper Eq. 3 and App H.
- k=1 skips the selector (`selection.trivial_selection`). Matches "At k=1, both forms reduce to the basic contrastive step and bypass selection" (paper §2.2.2).
- Anonymized, seeded-shuffle candidate ids; selector sees deliverables, task, and source only (`selection.assign_ids`, `selection.run_selection`).
- Reflection reads every candidate, not only the adopted one, and may answer `NO_ENTRY` (`selection.reflect_adoption`, `selection._distil`).
- Per-domain library, earlier entries only, whole trees stored (`schedule.run_domain`, `Library.retain`, `Library.add`). Matches paper App D.2.
- Evidence served as files, not inlined (`core/context.py` module docstring). Matches paper App D.2 and App H.
- Fixed stage prompts (`prompts.VERSION = "harness2-1"`); seeds shuffle task order only (`schedule.stream_order`; `docs/EXPERIMENTS.md`: "does not control
  model sampling"). Matches paper §2.3 and §3.1.
- Site teaser and results table match paper Table 1. The site states no limitations, rubric access, or cost.

### What the repo already says

n/a.

### Candidate landing spot

Nowhere as a list. Where a section describes a mechanism, follow the code at `1cfe254` (finals-only pool, trigger repair, per-executor surfaces),
and take rationale from the paper. D6 may feed a section 23 failure mode (see open questions).

### src/ candidate

n/a.

### Source anchors

- Code: `harness2/core/selection.py` (`selection_pool`, `run_selection`, `trivial_selection`, `reflect_adoption`, `_distil`);
  `harness2/core/schedule.py` (`run_task`, `_run_one_mode`, `run_domain`); `harness2/core/recursion.py` (`_accept_tree`, `_run_stage`);
  `harness2/core/tree.py` (`autotrigger`, `validate`); `harness2/core/prompts.py` (`POOL_MIXED_SELECT`, `POOL_FINALS_SELECT`, `_tts_*`, `propose`, `select`);
  `harness2/core/library.py` (`Library.add`, `has_experience`); `harness2/core/report.py`; `harness2/core/judge.py`; `harness2/core/context.py`;
  `harness2/benchmarks/jobbench.py` (`make_spec`, `_DEFAULT_TIMEOUT`, `jobbench_prompts`); `harness2/benchmarks/lab.py`; `harness2/benchmarks/workbuddy.py`;
  `harness2/substrate/codex.py` (`CODEX_SURFACE`); `harness2/substrate/opencode.py` (`OPENCODE_SURFACE`); `harness2/config.py`;
  `tests/test_workflow.py`; `third_party/lab_runner/opencode/run_opencode.py`; `third_party/wb_runner/agents/oc_agent.py`.
- Docs: `docs/EXPERIMENTS.md`; `docs/BENCHMARKS.md`; `README.md`.
- Paper: §2.1; §2.2.2; §2.3; §3.1; App B; App C; App D.1 to D.3; App H.

---

## Open questions for the placement grilling

1. Section 21 "Online run, offline learner" forbids lessons and prompt edits in the online loop. Does harness² enter as a named counterexample
   (online per-task edits, reflection after delivery, reset to H0), or does the paragraph become a spectrum?
2. Section 0 says to add a checker when no machine can check the result. Does a verifier-free improvement loop get a sentence there,
   or only in section 21?
3. New section 21 failure mode "candidates share a wrong premise": accept it, and with which mitigation wording (probes that challenge shared
   assumptions, repeated runs)?
4. The paper attributes "Agent = Model + Harness" to Weng, but the post does not contain the formula. Cite the paper for the formula and Weng only
   for the definition sentence?
5. The paper never cites ACE or Karpathy. Should section 21 still juxtapose them with harness², marked as this repo's synthesis, or keep them apart?
6. Where paper and code disagree (D1 to D4), the notes recommend describing the code at `1cfe254`. Agree?
7. D6 (unscored tasks dropped from the denominator): add a section 23 failure mode or a line in "Reading a difference", or leave it to Sources?
8. Ablations: one qualitative clause in section 21, or nothing beyond Sources?
9. Library growth: point to section 9 consolidation and section 7 decay, or leave it out since the paper only names it as future work?
10. The editable Codex hooks file can bind `PermissionRequest`, and Codex runs with its sandbox disabled. Does section 21 "Self-editing harness
    escapes its gates" cite this as a live case? Tickets r1 and r2 should confirm the edit-surface facts first.
11. Judge and improver share Claude Opus 4.8 on JobBench. Does this belong in section 23's same-family judge failure mode as an example,
    given the paper does not discuss it?

---

## Verification

Adversarial pass against the paper text (arXiv HTML converted to text, plus a WebFetch of the v1 abstract page), the Weng post text,
the site text, the clone at `1cfe254` (read only), and sections 0, 7, 9, 20, 21, 23. Default stance: suspicious.

Checked and held:

- Paper quotes in Concepts 1, 3, 4, 5: §1 challenges and central question, §2.1 definition and K = 8, §2.2.1 Propose, Probe, Compose wording,
  §2.2.2 "width rather than its depth" and "bypass selection", §2.3 "selecting an output alone", App A.1 "Benchmark scores are reserved",
  App B rubric and verifier lines, App D.2 retry and guard lines, App F, App G, App E.6, Table 5 C3 memory wording.
- Ablation table cells (Table 3) and deltas; main-table SD range 0.2 to 6.9 recomputed from 240 table cells.
- Weng quotes: the harness definition, "non-parametric system", reward hacking, gates outside the loop, "a harness for optimizing harnesses",
  and the absence of "Agent = Model + Harness" in the post. The paper does print that formula with a Weng citation (§2.1).
- No citation of ACE, Karpathy, Self-Harness, or arXiv 2605.30621 in the paper (searched by id and name). No web-access or reward-hacking text.
- D1 (finals pool, `POOL_FINALS_SELECT`), D3 (`autotrigger` before `validate`), D4 (`allow_subagents`), D5, D6, D7, D9 (grep: no ablation or
  baseline code), D10, D11 (`proposer_deliverables="names"`, `obs_trunc` 50,000), D12, D13 against code and docs.
- Every "the repo already says" quote: found in sections 0, 7, 9, 20, 21, 23 (some across bold markers or line breaks).

Changed:

- D2: JobBench's `selection_lead` is defined but never sent under the tts pack; the conclusion (no CONFORMANCE rule) stands.
- The "typically lack ground-truth labels" quote is from §1, not the abstract; added the abstract's own wording.
- Cross-references fixed: unscored tasks point to D6 (was D10); unrunnable ablations point to D9 (was D11).
- D8: added the paper's WorkBuddy timeout exception.
- src candidate: marked "keep an edit only if its probe changed behavior" as a stand-in, not harness²'s composer rule.

Not verified: the "9 of 12" Meta-Harness count, Fig. 6 to Fig. 12 numbers beyond the quoted text, and site `results.json` against Table 1.
