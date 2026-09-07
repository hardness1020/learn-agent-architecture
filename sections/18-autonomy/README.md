# 18 · Autonomy

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Run the loop with no human prompt: on idle, scan the board, claim a ready task, work it.

Autonomy is section 1 (the agent loop) running with no human prompt to start each turn.

Spawn a team and the obvious design is a lead that hands each worker its next task.

That does not scale. Ten unclaimed tasks mean ten manual assignments, and the lead becomes the bottleneck.

A worker that goes idle the moment it finishes also wastes the context it just loaded.

The fix is self organization, not central assignment.

Central assignment is still a real design, and most published multi-agent work describes that one.
In the manager pattern, each child agent is registered as a tool, and one manager hands out every subtask.
The manager holds the whole plan, so it can order the work, drop duplicate tasks, and stop the run early.
It also touches every task twice: once to hand it out, once to read the result.
The two designs cost different things. A manager gives one global ordering, and every task waits for the manager's turn.
A board gives throughput, and it needs locks because claims go stale. This section builds the board.

Autonomy must let an idle agent:

1. Notice it has nothing to do (the work phase reached `end_turn`).
2. Look at the shared board for tasks no one owns and nothing blocks.
3. Claim one without racing other idle agents for it.
4. Re-enter the loop on the claimed task, and repeat until the board is empty.

Leave this out and every agent is a puppet. It waits for a human or a lead to push the next prompt, so throughput is capped by how fast the dispatcher hands out prompts.

---

## Mechanism

![Mechanism diagram](assets/18-autonomy.png)

An outer loop wraps the agent loop.

The inner loop is the normal `while` from section 1. When it reaches `end_turn` the agent does not return. It enters a poll.

The poll drains two channels: a directed inbox (section 16) for messages addressed to this agent, and an undirected board (section 12) of tasks any idle agent can claim.

It checks them in priority order: a shutdown request first, then an inbox message, then a task on the board.

Whatever it finds becomes the next prompt, and the inner loop runs again.

- The inner loop ends on the model's `stop_reason`, the same signal as section 1.
- The poll checks shutdown first, so a stop is never buried under peer messages.
- Claiming is read, check, then write under a lock: pick an unowned, unblocked task, then write ownership before another agent can.
- A task is claimable only when its dependencies are `completed`, so no agent claims blocked work.

The shutdown request and its confirm are the section-17 protocol, so a stop is a handshake, not a kill.

### New: the idle poll

`autonomy.py` adds the outer loop and one poll pass. `next_action` drains the inbox once, then returns the first thing it finds, in priority order:

```python
def next_action(proto, team, store, me):               # src/autonomy.py
    inbox = team.drain(me)
    shutdown = next((m for m in inbox if _is(m, "shutdown_request")), None)
    if shutdown is not None:                            # checked first, so chat cannot starve a stop
        proto.reply(shutdown, "shutdown_approved")
        return ("shutdown", shutdown["content"].get("reason"))
    chat = [m for m in inbox if isinstance(m["content"], str)]
    if chat:
        chat.sort(key=lambda m: m["from"] != "lead")   # lead before peers; sort is stable
        return ("message", _fold(chat))                # section 16's shared fold helper
    task = claim_next(store, me)                        # else claim the next ready task
    return ("task", task) if task is not None else None
```

- It returns the first of: a shutdown (confirm and stop), folded chat, or a claimed task.
- Shutdown is checked before chat, so peer traffic cannot starve a stop (section 16).
- `claim_next` claims the first pending, unowned task; `TaskStore.claim` rejects blocked work and serializes the claim (section 12).
- `None` means idle: the outer loop sleeps and polls again.

### The claim, under a lock

The poll proposes a task; the lock decides who gets it. `claim_next` scans the board oldest first and proposes the first unowned, pending task:

```python
def claim_next(store, me):                             # src/autonomy.py
    for t in store.list():                             # oldest first
        if t["status"] == "pending" and t["owner"] is None:
            got = store.claim(t["id"], me)             # read, check, write under a lock
            if got["ok"]:
                return got["task"]
            # not ok: another agent won it, or it just became blocked; try the next
    return None
```

The check in `claim_next` is only a hint: two idle agents can read the same task as unowned at once. `TaskStore.claim` (section 12) settles it under a lock:

```python
def claim(self, tid, owner):                           # src/tasks.py, section 12
    with self._lock():                                 # fcntl.flock: one claimer at a time
        task = self.get(tid)
        if task["owner"] is not None:                  # someone already won: back off
            return {"ok": False, "reason": "already_claimed"}
        unmet = [b for b in task["blockedBy"]
                 if (self.get(b) or {}).get("status") != "completed"]
        if unmet:                                       # a dependency is not done yet
            return {"ok": False, "reason": "blocked"}
        task["owner"], task["status"] = owner, "in_progress"
        self._write(task)
        return {"ok": True, "task": task}
```

- The lock makes read, check, and write one atomic step, so the check cannot go stale before the write.
- The loser re-reads inside the lock, sees `owner` set, and gets `already_claimed`; `claim_next` moves to the next task.
- A blocked task is refused here too, so no agent claims work whose dependencies are not `completed`.
- This is the only place two threads contend for shared state. The rest of the poll is local.

### How it integrates

The outer loop wraps `run_turn` from outside, so the loop and the subagent path do not change:

```python
def run_teammate(team, store, me, lead, work):         # src/autonomy.py
    proto, prompt, claimed = Protocol(team, me), None, None
    while True:
        if prompt is not None:
            work(prompt, claimed)                      # inner loop (section 1) does the claimed task
            prompt, claimed = None, None
            team.send(me, lead, {"type": "idle", "reason": "available"})
        action = next_action(proto, team, store, me)   # poll: shutdown, message, or task
        if action is None:                             # idle: sleep, then poll again
            time.sleep(POLL_INTERVAL); continue
        kind, payload = action
        if kind == "shutdown":
            return "shutdown"
        if kind == "task":
            prompt, claimed = task_prompt(payload), payload
        else:
            prompt = payload
```

- This `run_teammate` is section 17's, with one more poll source: the task board. Shutdown (section 17) and chat (section 16) are unchanged.
- `work(prompt, claimed)` runs one inner loop to `end_turn` on the claimed task, then the agent announces it is available.
- A claimed task becomes the next prompt. When the poll finds nothing, the worker decides its own stop.
- That stop is either mode: idle until a shutdown handshake (section 17), or wind down after a bound of empty polls on a finite board.
- One worker runs here, but the loop is per agent. A real team runs a lead loop and many worker loops at once over the one shared board and inbox set.
- The lead takes one active step: it builds the team and the work by calling tools, then it is done.
- `TeamCreate` and `SpawnTeammate` are section 16's tools; `TaskCreate` posts the board (section 12).
- `SpawnTeammate` is `runtime.start(...)` (section 13): the lead's tool call starts a worker's autonomy loop on a thread.
- After the spawn, pulling work and deciding when to stop are each worker's own doing, not the lead's or the script's. The main process only waits for the workers to wind down.
- Forming the team, spawning, and posting the board are the model's decisions (sections 16 and 12); the autonomous claim is section 18's addition.

### Further reading

None of this is in `src/`. It comes from ai-agent-book and published research, and is not confirmed of the systems in the table.

**Asking a busy worker.** The poll tells a worker what to do next. It never tells the lead how a running worker is doing.

**Why a status call is weak.** A worker in the middle of a tool call is not listening for messages. So the call either hangs or comes back empty.
The worker that is truly stuck is the one that will not answer.

**Three ways that do work.** The first one needs the worker's help, and the last one needs none:

1. Ask by message. Drop a status request in the inbox (section 16). The worker answers on its next poll. This is accurate, and it works only while the worker still polls.
2. Read an agreed progress file. The worker adds one line per step to a path both sides know. The reader never interrupts the work.
3. Tail the saved trajectory. The runtime already writes every turn to disk (section 13). The lead reads those turns, and the worker does nothing at all.

**Spotting a stall.** The last two ways give that away for free. Check when the file was last written, because no new write means no new progress.
One threshold turns that into a decision. A slow tool call writes nothing while it runs, so set the threshold above the slowest call you expect. Past it, call the worker stuck.
That threshold is the trigger the stuck-busy failure mode was missing. The lead can take the task back, or start a shutdown handshake (section 17), instead of waiting forever.

**Budgets on the pool.** Nothing in the poll tells a worker to stop claiming. Every idle worker takes one more task.
So the run ends when the budget runs out, not when the work is done.

**What to hand out.** One published multi-agent system found that token use alone explained about 80% of the difference in how well its runs went.
So the unit to allocate is tokens, not turns. Four knobs attach to the board and the worker pool:

- Per task budget. Each task carries its own step cap and token cap, written when it is posted. One runaway task then cannot drain the whole run.
- Concurrency cap. Limit how many tasks sit in `in_progress` at once. The board already counts them, so a claim past the cap simply fails.
- Model placement. Put the strongest model where the thinking is hardest. Plan quality decides the result, so the lead gets that model and routine workers run a cheaper one.
- Preemption. A worker over budget, or stalled past the threshold, loses its task back to the board. Whoever claims it next starts from a clean state.

**Show the worker its budget.** An agent that knows what it has left spends it differently from one that just gets a bigger cap.
That result comes from the book's own experiment, so one source backs it. Treat it as something to test, not a number to copy.

---

## Per system

How an idle agent finds and claims its own work.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **Pros** | No dispatcher bottleneck. A watcher even picks up tasks made elsewhere. | Unattended runs are predictable, and the continuation state is durable. |
| **Cons** | Two idle agents can eye one task, so a lock settles the race. | One agent, one goal. The model itself judges when the goal is met. |
| **Why** | A lead that hands out every task becomes the bottleneck. | Autonomy is a budgeted permission level, not a mode. |
| **How: idle behavior** | A 500ms poll: shutdown, then unread messages, then a claim. | When fully idle it reserves the next round and queues one prompt. |
| **How: work claim** | Writes ownership of an unblocked task under a lock, so one claimer wins. | Reserves the next round against the goal's exact revision, or fails. |
| **How: self-organization** | Workers pull from the board (section 12). The lead synthesizes, never routes. | No board. The agent continues its own goal, and fan-out is capped. |

---

## Failure modes

- **Claim race.** Two agents read a task as unowned and both claim it, dropping one agent's work. Claim inside a file lock that makes the check and write atomic (section 12).
- **Starvation by chatter.** Peer chatter buries a shutdown request, so an agent that should stop keeps polling. Check for shutdown before regular messages (section 16).
- **Premature claim of blocked work.** An agent claims a task whose dependencies are not done, then stalls. Skip any task whose `blockedBy` holds an unresolved id (section 12).
- **Identity loss after compaction.** A long-running teammate is auto-compacted mid-run (section 8) and forgets its role. Preserve the system prompt so the role survives.
- **Stuck busy, or stuck idle.** A phase that never reaches `end_turn` never frees it; a poll with no exit spins. End on the stop signal (section 1); check abort each poll.
- **Undetected stall.** A worker holds a task, looks busy, and gets nowhere, so the board never frees it. Watch when its progress file was last written, then take the task back.
- **Unbounded pool spend.** Idle workers keep claiming, so the run ends only when the budget is gone. Give every task a step and token cap, and cap how many run at once.
- **Preemption double-write.** A released task is reclaimed while the old worker runs, so two agents write the same files. Release only after the stop is acked (section 17).

---

## Runnable

[`src/`](src/) carries 17 forward and adds:

- [`autonomy.py`](src/autonomy.py): the outer loop and idle poll over the section-12 board (section 16's `SpawnTeammate` starts each worker).
- [`test.py`](src/test.py): the single-worker mechanism, a `TeamCreate` check, a forced claim-race (16 threads, one task, one winner), a threaded pipeline, and a spawn-tool check.
- [`demo.py`](src/demo.py): the lead takes one step (`TeamCreate`, `TaskCreate`, `SpawnTeammate`); the workers then pull tasks off the board and stop themselves when it drains.

The single-worker `run_teammate` in the mechanism is the teaching reduction.

A real team runs a lead loop and several worker loops at once over one shared board and inbox set.

Section 13 starts work on threads; the section-12 and section-16 file locks keep the shared state safe under the race.

The concurrent demo and the claim-race test wire that up.

```bash
python sections/18-autonomy/src/test.py         # offline checks, no key
uv run python sections/18-autonomy/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code autonomy](https://github.com/yasasbanukaofficial/claude-code):
  `utils/swarm/inProcessRunner.ts` (`runInProcessTeammate`, `waitForNextPromptOrShutdown`, `findAvailableTask`, `tryClaimNextTask`, `sendIdleNotification`).
- [Claude Code claim and watch](https://github.com/yasasbanukaofficial/claude-code):
  `utils/tasks.ts` (`claimTask`, `claimTaskWithBusyCheck` under `proper-lockfile`), `hooks/useTaskListWatcher.ts`, `coordinator/coordinatorMode.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/bundle/headless/README.md`, `packages/goal/goal-round-driver/README.md`, `packages/workflow/tool-ralph/README.md`,
  `docs/subsystems/permission-presets.md`, `docs/subsystems/goal.md`.
- [learn-claude-code · s17 autonomous agents](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book · chapter 10](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  why a pull-style status query is weak, progress files and trajectory tailing, mtime stall detection, the manager pattern's central assignment,
  and team resource scheduling (per subtask budgets, concurrency caps, model placement, preemption).
  The budget-awareness result comes from the book's own experiment, so it is single-source.
- [Plan-and-Act](https://arxiv.org/abs/2503.09572) (Erdogan et al., 2025): splitting a planner from an executor, with plan quality as what drives the outcome.
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) (Anthropic, 2025):
  token use alone explains about 80% of the variance in performance, with tool call count and model choice next.
