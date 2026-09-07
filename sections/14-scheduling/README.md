# 14 · Scheduling

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Start agent turns from a clock, not only from user input.

Background work still needs someone or something to start it. Many tasks should run later or repeat: a report, a reminder, or a polling task.

Scheduling stores a future trigger. When it fires, it enqueues a prompt. The normal loop handles that prompt as a new turn.

Scheduling must:

1. Store a schedule outside one turn.
2. Watch time independently of the loop.
3. Enqueue a prompt when the schedule fires.
4. Optionally persist schedules across restarts.

Without this layer, the agent can only react to user input.

---

## Mechanism

![Mechanism diagram](assets/14-scheduling.png)

Separate the clock from the loop. The scheduler watches time. It does not call the model directly.

At fire time, the scheduler only enqueues a prompt. The driver drains the queue between turns, when no turn is in flight,
and runs each prompt through the same agent loop that handles user input.

- A schedule is data: a prompt to run, a fire time, and an optional repeat interval. The scheduler stores each one as a task.
- A one-shot fires once and then deletes itself.
- A recurring schedule re-arms to the next interval.
- A durable schedule survives restart, but it does not fire while the host is off.
- A heartbeat is a recurring schedule that asks a question. It wakes, checks a source, and usually decides there is nothing to say.

### New: the scheduler and fire queue

`tick` checks due tasks. Firing means enqueueing a prompt:

```python
def tick(self):                                       # src/scheduler.py; called by a daemon thread
    now = self._clock()
    for tid, t in list(self._tasks.items()):
        if now >= t["due"]:
            self._pending.put({"prompt": t["prompt"], "channel": t.get("channel")})
            if t["every"]:                            # enqueue, do not run the model here
                t["due"] = now + t["every"]
            else:
                self._tasks.pop(tid, None)
    self._save()                                      # durable tasks only
```

- The clock is injectable, so tests use a fake clock.
- `run()` calls `tick` on a daemon thread.
- `_save` persists durable tasks to JSON.
- A new `Scheduler` on the same path reloads durable tasks and resumes ids.

### New: delivering the answer

A fired run has no human waiting, so the answer needs a route out. Each task can name a channel.
The channel is a field on the task: `create(..., channel="console")` stores it, and `tick` enqueues it with the prompt.
Each drained item is already `{"prompt": ..., "channel": ...}`, so the driver never looks up where an answer goes.

`deliver` routes the turn's answer (Hermes delivers cron output to the job's chat platform):

```python
SILENT = "[SILENT]"                              # a fired run may decide nothing is worth sending

def deliver(channels, fired, text) -> bool:      # src/scheduler.py
    if not fired.get("channel") or text.lstrip().startswith(SILENT):
        return False
    channels[fired["channel"]](text)
    return True
```

- `channels` maps a channel name to a send callable (print here; a real adapter is section 19's job).
  The task names the channel; the driver owns the map. Neither knows the other's details.
- When the answer starts with `[SILENT]`, `deliver` skips the channel send. This is the convention for a scheduled check that found nothing worth telling the user
  (a poll that saw no change). The driver still holds the full text and can log it.
- No channel means the answer stays local, the pre-delivery behavior.
- The `bool` return lets the driver fall back (the demo prints undelivered answers) instead of losing the answer silently.

### Heartbeat

Some sources never push. A mailbox with no webhook, a page with no feed, a service that only answers when asked.
For those the only trigger left is the clock. The pattern is a heartbeat: a recurring schedule whose prompt tells the agent to look, not to act.
Check the source, decide whether anything changed enough to be worth a message, and say nothing otherwise.

A heartbeat run that finds nothing worth reporting answers `[SILENT]`. By the rule above, `deliver` then sends nothing.
The tick costs one model call and no message, so the schedule can run often without flooding the channel.

A heartbeat and a cron entry use the same parts here: a prompt, a repeat interval, and a channel. Only the prompt differs.
A cron prompt gives an order. A heartbeat prompt asks a question.

### How it integrates

Scheduling is two halves. `tick` runs on its own daemon thread (section 13's background execution); it never touches the model and only enqueues on fire:

```python
def run(self):                                        # src/scheduler.py; started by sched.run()
    def loop():
        while not self._stop.wait(self.CHECK_INTERVAL):   # wakes once per second
            self.tick()
    threading.Thread(target=loop, daemon=True).start()    # daemon: never keeps the process alive
```

The turn itself runs in the foreground: the driver drains the queue between turns and calls `run_turn` once per fired task:

```python
for task in sched.drain():                            # src/demo.py · between turns
    messages = [{"role": "user", "content": task["prompt"]}]
    deliver(channels, task, run_turn(messages, model, reg, session))
```

A fired prompt becomes a new user-style turn. It uses the same loop, permissions, hooks, memory, context management, and recovery paths. Its answer routes to the task's channel.

### Further reading

None of this is in `src/`. It comes from ai-agent-book, and is not confirmed of the systems in the table.

**The limits of a clock.** A heartbeat has one setting that matters: the interval.
It sets the bill and the worst case delay at once, and those two pull against each other.
A short interval wakes the model often and finds nothing most times. A long interval is cheap and late.
No interval fixes this. A clock samples state instead of watching events, so it knows when it last looked, not when the thing happened.

**Prefer push where you can get it.** When the source can call the agent, the trigger fires as the event happens and the polling cost drops to zero.
So the order is push where the source supports it, heartbeat where it does not, and cron for work that really is time-based, like a Monday report.
Section 19 covers the inbound push side.

---

## Per system

How each agent decides when to run scheduled work.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Simple and private. Durable schedules survive restart. | Fires unattended, no hosted service. | Reminders replay with the session. Missed fires collapse to one turn. |
| **Cons** | Ticks while a session runs; remote triggers need a service. | Needs a gateway and locks against double fire. | Fixed rates only. Nothing fires cold. |
| **Why** | Assumes a local session is running. | The gateway is a server, so schedules fire unattended. | A reminder is conversation state, so the session log owns it. |
| **How: trigger** | Cron, sleep, and remote triggers on a ticker. | Cron on a gateway tick, in the user's timezone. | After a delay, at a time, or every five minutes at most. |
| **How: durability** | Session state, or a JSON file with a lock. | A shared JSON job store with an atomic claim. | Session-log events. A fork keeps history, drops reminders. |
| **How: wakeup** | Fired prompts queue and run between turns. | Due jobs run in parallel and deliver to chat. | Due work waits for idle, then queues one turn. At-least-once. |

---

## Failure modes

- **Double fire.** A fast tick can match the same cron minute more than once. Track the last fired minute.
- **Many schedules fire together.** Add deterministic jitter to recurring tasks.
- **Durable means always-on.** Local durable schedules only survive restart. Use remote triggers or an OS timer for offline firing.
- **Bad cron expression.** Validate on create and skip invalid loaded entries.
- **Loop is busy.** Enqueue the prompt and drain it between turns.
- **Alert fatigue.** A heartbeat that reports on every tick teaches the user to ignore it. Let the prompt decide what is worth sending and stay silent otherwise.
- **Events between ticks.** A clock samples state. A change that appears and reverts between two ticks is invisible. Read a log or a cursor, or move the source to push.

---

## Runnable

[`src/`](src/) carries 13 forward and adds:

- [`scheduler.py`](src/scheduler.py): a scheduler, fire queue, recurring re-arm, one-shot delete, durable JSON store, and channel delivery (`deliver`, `SILENT`).
- [`test.py`](src/test.py): uses a fake clock to test one-shot, recurring, reload, and delivery behavior.
- [`demo.py`](src/demo.py): schedules a prompt one second out, runs it as a new turn, and delivers the answer to a console channel.

The loop is unchanged. Scheduling starts turns from outside it.

```bash
python sections/14-scheduling/src/test.py         # offline checks, no key
uv run python sections/14-scheduling/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `tools/ScheduleCronTool/`, `tools/RemoteTriggerTool/`, `tools/SleepTool/`, `utils/cronScheduler.ts`, `hooks/useScheduledTasks.ts`, `utils/queueProcessor.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `cron/scheduler.py` (`tick`, `_resolve_cron_disabled_toolsets`), `cron/jobs.py` (`_jobs_lock`, `claim_dispatch`), `hermes_time.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/schedule/schedule/src/runtime.ts`, `packages/schedule/schedule/src/persistence.ts`, `packages/schedule/schedule/src/tools.ts`,
  `docs/subsystems/schedule.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s14_cron_scheduler](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, Chinese original canonical.
  Heartbeat wakeups with judgment, alert fatigue, and the limits of time-driven triggers.
