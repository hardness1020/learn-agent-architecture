# 0 · Harness thesis

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> The model decides what to do. The harness gives it tools, state, and limits.

The model owns reasoning, tool choice, and when to stop. The harness is the code around the model: the loop, tools, memory, permissions, and interfaces.

A model call alone is one response to one input. It can decide to act, but it cannot act by itself. It has no durable state, no tool runner, no file access, and no permission gate.

The harness must:

1. Give actions a place to run.
2. Give the model useful observations.
3. Gate side effects before they reach the world.
4. Persist state so later calls build on earlier calls.

Without a harness, the model can only answer. It cannot run tools, observe results, or remember work across calls.

---

## Mechanism

![Mechanism diagram](assets/00-harness-thesis.png)

This section is about decomposition. A small model call sits at the center. The harness supplies its inputs and handles its outputs.

The model owns judgment. The harness owns the environment.

The loop in section 1 is the core control flow. Other sections add inputs, checks, or state around it:

- Section 2 adds the tool runtime and dispatch.
- Section 3 adds permissions and sandboxing.
- Section 4 adds hooks that intercept lifecycle events.
- Sections 8 and 9 add context management and memory.
- Section 10 assembles the system prompt each turn.
- Later sections add tasks, background work, scheduling, and isolation.

These parts do not replace the loop. They feed it, gate it, or persist state for it.

### More harness is not better

Each layer covers something the current model cannot do alone. That gives every layer two costs:

1. More code to maintain, and more places where bugs can appear.
2. A design tied to one model generation. A newer model may plan, recover, or verify on its own. Forcing the old workaround then lowers performance instead of raising it.

So harness engineering is not only adding. When the model changes, re-evaluate each layer: keep what still helps, delete what the new model covers.
Sections 20 and 21 build the measurements for this. mini-swe-agent is the extreme case: almost no harness, so almost nothing to re-evaluate.

deepseek-harness answers the same problem from the other end. Every part is a plugin, the loop included, and no core is privileged over the rest.
Each capability is published at a named seam, and one plugin claims it: tools at one seam, permissions at another, context handling at a third.
So re-evaluating a layer is a config change, not a fork. Deleting a layer means not loading that plugin.

### Further reading

Two framings from ai-agent-book. Claims to test, not this repo's own findings.

**Testing the boundary.** Where the line falls between model and harness changes as models get better. The book makes two claims you can check:

- **Scaffolding tracks model strength.** A weak model needs the forced plan, the retry ladder, and the scripted check. A strong model does that work itself.
- **The reading threshold is model policy.** When to stop reading code and start editing is learned in training. A prompt line or a step limit nudges it. Neither sets it.

**Both claims turn into one test.** Run the same scaffold on two model generations and the scores can move in opposite directions.
One book experiment reports this, so test the direction rather than trusting the size.
Then ask of each layer which side of the line it sits on. If the model already makes that call, the layer is a duplicate and only burns tokens.

**Where agents work first.** Two things decide whether a task suits an agent today: how exactly you can state the goal, and whether a machine can check the result.
Coding scores high on both. A ticket or a failing test states the goal. Tests, types, linters, and git say when the work is done.
People built that infrastructure for themselves, and an agent reuses it as a ready-made verification harness. This is why coding agents matured first.

**When one property is missing.** The task does not get vaguely harder. It fails in a specific way, and there are two cases:

- **Clear goal, no automatic check.** Rewrite a page so it reads better. The loop has no stop condition, so it calls the work done because nothing says otherwise.
- **Automatic check, no clear goal.** Clean up a module. The loop aims at the check instead of the goal. It proves nothing broke, which is not what you asked for.

**Each case needs a different fix.** Section 21 builds the check when the domain has none. No check fixes a goal you cannot state.

---

## Per system

What the model decides versus what the surrounding code builds.

| | Claude Code | mini-swe-agent |
| --- | --- | --- |
| **Pros** | The harness adds safety, persistence, subagents, and on-demand knowledge. | Almost no harness code, so almost nothing to maintain. |
| **Cons** | The harness becomes the main code surface. Most behavior and most bugs live there. | Every capability beyond running bash must come from the model. |
| **Why** | A model call cannot act by itself, so the harness owns the environment. | Assumes one bash tool is enough. Hooks, skills, memory, and tasks are absent by design. |
| **How: model owns** | Judgment, tool choice, and stop decisions. Sees tool names, schemas, and results. | Judgment, editing tactics, and when to submit. |
| **How: harness owns** | Loop, tools, permissions, hooks, knowledge, tasks, and coordination. | One loop, one bash tool, a confirm gate, plus step and cost budgets. |
| **How: size signal** | Most code sits outside the model call. | The whole agent class is about 150 lines. |

---

## Failure modes

- **Crediting the model for harness behavior.** Permission checks and error recovery are harness behavior. Fix the harness when they fail.
- **Hard-coding decisions the model should make.** Rigid tool order and scripted planning can fight the model. Let the model decide when judgment is required.
- **Too little harness.** A loop with no tools, permissions, or context management keeps the model at chatbot behavior. Add the missing layer.
- **Too much harness.** Each layer adds maintenance, and a layer built for an older model can hold a newer one back. Re-evaluate on model change, delete what no longer helps.
- **Treating a model policy as a harness setting.** The model learns when to stop gathering information. A prompt rule only nudges it. Measure the layer before you keep it.
- **Running an agent where no machine can check the result.** The loop cannot tell a finished task from a wrong one. Add a checker, or keep a person in the path.
- **Mixed responsibilities.** Permission logic inside tool execution is harder to test and replace. Keep clear contracts such as `Tool.ts` and `PreToolUse`.

---

## Sources

- [Claude Code source (`cc-src/src`)](https://github.com/yasasbanukaofficial/claude-code): `QueryEngine.ts`, `query/`, `Tool.ts`, `tools/`, `hooks/`, `types/permissions.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`, `environments/local.py`, protocols in `__init__.py`.
- [mini-swe-agent README](https://github.com/swe-agent/mini-swe-agent): the case for a minimal harness as models improve.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/capability-seams.md`, `docs/cordis-primer.md`, `docs/subsystems/core.md`.
- [learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter5.md`, Chinese original canonical. Boundary framing and the task quadrant, both single-source.
