# 11 · Error recovery

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Classify failures, then retry, adjust, or stop.

An agent run can span many model calls. Any call can fail because of network issues, overload, rate limits, output limits, or context overflow.

Model calls are only one source of failure. One study of production coding agents sorts failures into four layers:

- **API.** Timeouts, rate limits, and overload.
- **Tool.** A command that exits non-zero, or a handler that raises.
- **Context.** Prompt overflow, or a message history the API rejects.
- **Control flow.** Steps that repeat and get nowhere.

Work out the layer first, then start counting attempts. Counting first spends the budget on errors that no retry can fix.

The loop needs different responses for different failures:

1. Retry transient errors.
2. Adjust and retry when the prompt or output limit is the problem.
3. Stop when the error is not recoverable.

Without recovery, one temporary API failure can end a long task.

---

## Mechanism

![Mechanism diagram](assets/11-error-recovery.png)

Wrap the model call in a retry helper. The helper classifies the failure, then takes a bounded action.

- Transient status codes back off and retry.
- Prompt overflow runs a compaction callback once, then retries.
- Repeated overload can trigger a fallback model.
- Unknown or non-retryable errors are raised.

### New: classification, backoff, and the retry helper

```python
RETRY_STATUS = {408, 409, 429}                         # src/recovery.py; these plus any 5xx

def should_retry(status) -> bool:
    return status in RETRY_STATUS or (status is not None and 500 <= status < 600)

def retry_delay(attempt, retry_after=None) -> float:   # exponential backoff + jitter
    if retry_after is not None:
        return float(retry_after)
    base = min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)
    return base + base * 0.25 * random()
```

Overflow is checked before generic status handling. A `prompt_too_long` error can be recoverable if compaction can shrink the prompt.

```python
def _status(e):
    return getattr(e, "status_code", None)

def _is_overflow(e) -> bool:
    return getattr(e, "overflow", False) or "prompt is too long" in str(e).lower()
```

`with_retry` holds the per-attempt state:

```python
def with_retry(call, on_overflow=None, fallback_model=None,
               max_retries=DEFAULT_MAX_RETRIES, sleep=time.sleep):
    consecutive_529 = 0
    overflowed = False
    for attempt in range(1, max_retries + 2):
        try:
            return call()
        except Exception as e:
            if _is_overflow(e):
                if on_overflow is None or overflowed:
                    raise
                overflowed = True
                on_overflow()
                continue
            status = _status(e)
            if status is None:
                raise
            if status == 529:
                consecutive_529 += 1
                if fallback_model and consecutive_529 >= MAX_529_RETRIES:
                    raise FallbackTriggered(fallback_model)
            if attempt > max_retries or not should_retry(status):
                raise
            sleep(retry_delay(attempt, getattr(e, "retry_after", None)))
```

### How it integrates

The loop wraps its model call:

```python
response = recovery.with_retry(
    lambda: model(messages, registry, system),
    on_overflow=lambda: _reactive_trim(messages),
    fallback_model=fallback_model)
```

- Recovery wraps only the model call.
- `_reactive_trim` mutates `messages[]` in place for one overflow retry.
- When recovery gives up, the error is surfaced instead of hidden.

### Further reading

None of this is in `src/`. It comes from ai-agent-book, and is not confirmed of the systems in the table.

**Catching a loop that never raises.** Say the agent runs a test file, reads the same error, and runs the same test file again.
Nothing throws, so no retry path fires and no bound is reached. This is a control-flow failure, and it needs a detector of its own.

The detector is a fingerprint: the tool name plus its arguments. A fingerprint that repeats is the agent redoing the same call.
A step cap does end the run, but only once the whole budget is gone. A fingerprint counter ends it in a few steps, and it can name the call that is stuck.

Recovery paths need counters of their own too. Count failures per path, so a path that keeps failing trips its own breaker instead of waiting for the global cap.

**Killing a stream that went quiet.** A stream can connect, send a few tokens, and then stop.
The connect timeout has already passed by then, so nothing fires and the loop waits.

The fix is a second timer. Add an idle watchdog beside the connect timeout, and cancel the call when no token arrives inside the window.
The retry helper then treats the cancellation as an ordinary transient failure.

**Repairing a broken message history.** A crash mid turn can leave a `tool_use` block with no matching `tool_result`.
The next request then fails on message shape, not on the work, and it keeps failing until the pairs are fixed.

What repair means depends on what the transcript is for. There are two answers:

- **A product harness repairs.** It adds a placeholder result saying the call was interrupted, and the run continues.
- **A training-data harness refuses.** A made-up result would teach a step that never ran.

**How much failure the caller should see.** Recovery is not one decision. Grade it by how visible the failure should be:

1. **Retry quietly.** The caller sees only the final result.
2. **Degrade and continue.** Return a smaller result, and say what is missing.
3. **Surface the failure.** List the attempts, so the model can try another path.

Errors from the first two grades need quarantine. Hold them inside the helper, and release them only when recovery gives up.
An error that reaches the model early looks final, and the model may redo work that had already succeeded.

**Stopping recovery from feeding itself.** An error path can trigger a hook, a summary, or a notification.
That work calls the model again, fails again, and the new failure triggers the same path once more.

Two rules break the chain. Turn off side-effect logic on error paths, and keep a recursion depth counter for whatever survives.
Background calls get no retries at all: they sit off the critical path, so a retry only spends quota the main loop needs.

**Where the bounds come from.** Every bound in this section is a number someone picked: how many retries, how many strikes, how long the idle window.
Pick each one from measured failures, not from intuition. The book's three-strike compaction bound came from production data on repeated recovery failures.

---

## Per system

Recovery wraps the model call. The loop body stays the same.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Specific paths save more runs than a blanket retry. | Three bounded paths. A crash leaves a full trajectory. | Retries are logged, so a resumed session knows them. |
| **Cons** | More branches and bounds to maintain. | Saves fewer runs. Overflow aborts, and so do three format errors. | No fallback model. Its always mode retries forever. |
| **Why** | One temporary API failure should not end a long task. | Retry, hand format errors back, name the exit. | The log is the truth, so recovery replays a fresh turn. |
| **How: retry** | Backoff on 429, 408, 409, 5xx; `retry-after` wins. | tenacity backoff, 4 to 60 seconds, 10 attempts. | An error event after the failed turn, then a fresh one. |
| **How: token handling** | Raise the output cap, continue after a `max_tokens` stop, or compact. | None. Overflow aborts the run. | One overflow code: prune, then summarize. |
| **How: model fallback** | Fallback model after repeated overload (529). | None. | None. The retry turn rebuilds the same request. |

---

## Failure modes

- **Retry storm.** Many clients retrying overload can make load worse. Limit retries and respect `retry-after`.
- **Infinite recovery.** Escalation, continuation, and compaction can loop. Bound each path.
- **Overflow cannot shrink.** If one reactive compaction fails, stop instead of compacting forever.
- **Error disappears.** A swallowed error leaves the transcript with a missing result. Surface failure after recovery is exhausted.
- **Stop hook repeats an API error.** Skip stop hooks for API-error messages.
- **Stuck without an error.** A call that keeps repeating raises nothing, so no retry path fires. Count repeated tool-plus-args fingerprints, and stop the run.
- **Silent stream stall.** A stream can open and then go quiet. The connect timeout has already passed, so nothing fires. Add an idle watchdog.
- **Repair pollutes the record.** A placeholder `tool_result` keeps a product run alive. It also records a step that never ran. Do not repair a transcript kept as training data.
- **Intermediate error leaks.** An error shown before recovery finishes looks final, and the model redoes the work. Hold it inside the helper until recovery gives up.

---

## Runnable

[`src/`](src/) carries 10 forward and adds:

- [`recovery.py`](src/recovery.py): retry classification, backoff, overflow handling, and fallback trigger.
- [`loop.py`](src/loop.py): wraps its model call in `with_retry`.
- [`test.py`](src/test.py): drives each path with a fake flaky call.
- [`demo.py`](src/demo.py): injects one simulated overload in a live run.

```bash
python sections/11-error-recovery/src/test.py         # offline checks, no key
uv run python sections/11-error-recovery/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `services/api/withRetry.ts`, `query.ts`, `services/api/claude.ts`, `services/api/errors.ts`, `query/tokenBudget.ts`, `utils/context.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `models/utils/retry.py`, `models/litellm_model.py`, `run()` and `max_consecutive_format_errors` in `agents/default.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/llm/llm-retry/README.md`, `packages/llm/llm-retry/src/types.ts`, `packages/core/agent-loop/src/agent.ts`,
  `docs/subsystems/llm-streaming.md`, `docs/subsystems/core.md`, `docs/subsystems/persistence.md`.
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  the four-layer failure taxonomy, tool-plus-args loop fingerprints, the idle watchdog, `tool_result` pair repair with its product versus training-data split,
  graded recovery with error quarantine, and the death-spiral defenses. Its footnote ch5-3 sources the taxonomy from a study of production agents,
  Claude Code among them, and warns that the implementation moves fast. It also sets its three-strike compaction bound from measured production failures.
- [learn-claude-code · s11_error_recovery](https://github.com/shareAI-lab/learn-claude-code): section framing.
