# 8 · Context management

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Keep long sessions under the context limit.

`messages[]` grows during a run. Each tool result, assistant reply, and user turn adds more text. A long session will eventually reach the model's context limit.

Context management keeps the session usable. It removes, stubs, persists, or summarizes old content before the next model call.

When context fills:

1. The API can reject the request.
2. Calls become slower and more expensive.
3. Old, less useful content competes with current task information.

The third item has a name: context rot. The model finds the right fact less often as unrelated text piles up.
This starts well before the window is full. The agent keeps running. It just decides worse.

So compaction is not only about fit and cost. In-context learning works more like retrieval than like reasoning.
The model can find a fact that is written down. It is worse at combining facts spread over dozens of turns.
Writing the conclusion down once is cheaper than making the model derive it again on every call.
So a good summary improves answers even when the window still has room.

Without this layer, long tasks fail once the prompt no longer fits.

---

## Mechanism

![Mechanism diagram](assets/08-context-management.png)

Use cheap reducers before summarization. Cheap reducers are local and mostly lossless. Summarization costs a model call and can lose detail.

Claude Code uses a layered order:

```text
budget   -> persist huge tool results to disk, leave a preview
snip     -> drop stale middle turns, keep head + recent tail
micro    -> replace old tool-result bodies with a stub
collapse -> optional independent context system
auto     -> LLM summarizes the whole history into one message
--- on prompt_too_long despite the above ---
reactive -> truncate the head and re-summarize, with a retry cap
```

Order matters. For example, a large tool result should be persisted before any pass replaces its body with a stub.

### New: the reduction passes

```python
def manage(messages, summarizer=None):                 # src/context.py, run every turn
    _budget(messages)                                  # persist huge results   (lossless)
    _micro(messages, KEEP_RECENT)                      # stub old result bodies (cheap)
    if summarizer and estimate_tokens(messages) > TOKEN_LIMIT:
        return _auto(messages, KEEP_RECENT, summarizer)  # summarize history (lossy, last resort)
    return messages
```

- `manage` runs cheap passes each turn.
- `_budget` writes oversized tool results to disk and leaves a short preview.
- `_micro` stubs old tool-result bodies.
- `_auto` keeps the first turn and recent tail, then summarizes the middle.
- `summarizer=None` disables lossy summarization in the demo.

### How it integrates

Context management runs before each model call:

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)   # 8 · keep context under the window
    response = model(messages, registry)
    ...
```

This section changes the loop body itself. Earlier sections added tools or dispatch behavior and left the loop alone.
Context reduction must run before every model call, so it has to live in the loop.

The loop still keeps the same invariant: it calls the model with a valid `messages[]`, then appends the response and any tool results.

### Contrast: spilled tool output

Claude Code and this section's `_budget` both shrink a huge tool result in place. The text that gets cut is gone.

deepseek-harness never edits what happened. The session log only gets appended to, and the messages the model sees are a projection of that log.
A reduction is one more logged event that says which span to replace, so a resumed or forked session replays the same view.

Big tool output takes a separate path, before any of that. A result over an inline byte cap goes to a spill store the moment the tool returns.
The store saves the full text and returns a locator. What stays in context is a head and tail preview, that locator, and a hint to read or grep it.
So the output is still reachable: the model asks for the file when it needs the rest.

[`src/spill.py`](src/spill.py) is a strip-down of this. It is a contrast demo, not wired into `manage()`, so later sections carry the same passes forward.

### Further reading

None of this is in `src/`. It comes from ai-agent-book, and is not confirmed of the systems in the table.

**A stub must be the same string every time.** The text that replaces a tool result is part of the prefix, so it has to stay byte-identical.
Pick it at the first replacement and reuse it, including after a session is restored from disk.
A stub that re-renders with a new timestamp or a new path changes the prefix, and the cache after it is gone.

**Compression and the cache want opposite things.** Compaction rewrites the history. The cache pays off only when the history is left alone.
Every edit invalidates the cache from the edit point onward, so the next call re-reads the whole prefix.
Trim a little on every turn and that rebuild happens every turn. Do one larger reduction at a token threshold and it happens once.
Either way, compaction runs between API calls, never inside one.

**The API can run this pass on the server.** Context editing in the Claude API drops older tool results from the prefix, so the harness ships no code for it.
It still rebuilds the cache once. That puts it near the overflow end of the order rather than on every turn.

**Write the summary for the current task, not for the whole session.** A recap of everything that happened is not what the next call needs.
Ask one question instead: what does the next call still need? Keep these, highest priority first:

- Architecture and design decisions already made.
- Files created or changed, and what changed in them.
- Pass and fail status of the last checks or tests.
- Open TODOs and the current step.

Raw tool output goes first when something has to go. The budget pass already wrote the large results to disk, so the agent can read one back when it matters.

---

## Per system

How each agent decides to make room and what it removes.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Long sessions survive. Reductions are cheap and outputs re-readable. | Nothing to schedule or tune. Easy to audit. | History is never destroyed. |
| **Cons** | Passes need ordering rules. A summary can drop detail. | History only grows. A long run dies on overflow. | The log grows on disk, and needs locks and folds. |
| **Why** | Interactive sessions are open ended, so the window fills. | Assumes a budget ends the run first (section 21). | The log is the truth, so only the view shrinks. |
| **How: trigger** | Token threshold, plus a fallback on `prompt_too_long`. | Every observation, at render time. | Measured pressure each step, plus confirmed overflow. |
| **How: strategy** | Cheap reducers first (persist, stub), summary last. | Truncate long output to a head and a tail. No compaction. | Spill, prune, then a summary event. |
| **How: budget** | Reserve output and safety buffers. | 10k characters per observation. | Ratios per routed model: compact at 0.8, keep 0.16. |

---

## Failure modes

- **Summary loses needed detail.** Persist full outputs and re-read files when needed.
- **Compaction fails repeatedly.** Use a retry cap or circuit breaker.
- **One huge turn overflows anyway.** React to `prompt_too_long` with a bounded last-resort trim.
- **Wrong pass order loses data.** Persist large results before stubbing old results.
- **Broken tool pairs.** Do not split a `tool_use` from its matching `tool_result`.
- **Stub text drifts.** A preview that re-renders with a new timestamp or path changes the prefix and drops the cache. Freeze the string at first use.
- **Trimming on every turn.** Each edit invalidates the cache after the edit point, so many small reductions cost more than one batched pass. Trigger on a threshold.
- **The model trusts the summary.** Injected state is read as fact and rarely re-checked. Leave pointers to the persisted originals so a wrong summary can be caught.

---

## Runnable

[`src/`](src/) carries 07 forward and adds:

- [`context.py`](src/context.py): `budget`, `micro`, and `auto` passes run through `manage`.
- [`loop.py`](src/loop.py): calls `context.manage()` at the top of every turn.
- [`spill.py`](src/spill.py): the deepseek-harness contrast: an oversized result is saved whole, and context keeps a preview plus the path.
- [`test.py`](src/test.py): checks each pass in isolation, plus a spill that keeps the full text readable.
- [`demo.py`](src/demo.py): drives the loop with context management wired in.

```bash
python sections/08-context-management/src/test.py         # offline checks, no key
uv run python sections/08-context-management/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `services/compact/autoCompact.ts`, `microCompact.ts`, `timeBasedMCConfig.ts`, `compact.ts`, `utils/toolResultStorage.ts`, `query.ts`, `query/tokenBudget.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): the observation template in `config/mini.yaml`, `abort_exceptions` in `models/litellm_model.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/compaction/compaction/src/index.ts`, `packages/compaction/compaction-basic/README.md`, `packages/llm/token-meter/src/index.ts`,
  `packages/spill/spill/src/index.ts`, `packages/spill/spill-policy/README.md`, `docs/subsystems/compaction.md`, `docs/subsystems/session.md`.
- [learn-claude-code · s08_context_compact](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book · chapter 2](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  context rot, in-context learning as retrieval, the compression and cache interplay, task-aware compression with retention priorities,
  API-level context editing, the frozen tool-result stub, and the finding that models read an injected summary as fact.
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) (Liu et al., TACL 2024): retrieval accuracy drops for facts placed in the middle of a long context. Grounds context rot.

Inferred; not fully present in the Claude Code source repo above:

- `snipCompact.ts`: only the `snipCompactIfNeeded(messages)` call site is visible.
- `reactiveCompact.ts`: the reactive path appears to live in `compact.ts`.
