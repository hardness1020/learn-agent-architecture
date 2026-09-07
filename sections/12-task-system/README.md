# 12 · Task system

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Store work as durable tasks with dependencies.

The todo list from section 5 lives only in memory and disappears when the process ends. It also cannot say which task must wait for another.

A task system stores work as records on disk. Each record can have dependencies. Workers claim tasks when their blockers are complete.

The task system must:

1. Store each unit of work as a durable object.
2. Represent ordering as data.
3. Survive turns, sessions, and crashes.
4. Let only one worker claim a task.

Without this layer, the plan exists only in the current context window.

---

## Mechanism

![Mechanism diagram](assets/12-task-system.png)

A task is a JSON record on disk. `blockedBy` and `blocks` are dependency edges. A file lock serializes claims.

- IDs are sequential and never reused.
- Create, get, update, and list are plain CRUD.
- `claim` is the gate. It checks ownership and blockers before assigning an owner.
- The disk graph stores the plan. A separate runtime can track active background work.

### New: the task store and claim gate

`create` allocates an id and writes a task:

```python
def create(self, subject, blocked_by=()):              # src/tasks.py
    tid = self._next_id()
    task = {"id": tid, "subject": subject, "status": "pending",
            "owner": None, "blockedBy": list(blocked_by), "blocks": []}
    self._write(task)
    ...                                                # keep the reverse `blocks` edge in sync
    return task
```

`claim` is locked. That makes check-then-set safe across workers:

```python
def claim(self, tid, owner):                           # src/tasks.py
    with self._lock():                                 # fcntl.flock, exclusive
        task = self.get(tid)
        if task["owner"] is not None:
            return {"ok": False, "reason": "already_claimed"}
        unmet = [b for b in task["blockedBy"]
                 if (self.get(b) or {}).get("status") != "completed"]
        if unmet:
            return {"ok": False, "reason": "blocked"}
        task["owner"], task["status"] = owner, "in_progress"
        self._write(task)
        return {"ok": True, "task": task}
```

### How it integrates

Task tools are thin wrappers over the store:

```python
for t in task_tools(TaskStore(dir)):                   # src/demo.py
    reg.register(t)                                    # TaskCreate / TaskUpdate / TaskGet / TaskList
```

The loop does not change. The model calls `TaskCreate`, `TaskUpdate`, `TaskGet`, and `TaskList` like any other tools.

---

## Per system

How the durable task graph is shaped and advanced.

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **Pros** | File-backed tasks survive crashes and support many workers. | Task state rides the session log, so replay, fork, and resume come free. |
| **Cons** | Costs reads, writes, and locks. Records need validation. | No dependency edges and no claim gate. One goal per session. |
| **Why** | An in-memory list dies with the process, so the plan must outlive it. | The session log is the only source of truth, so task state is events. |
| **How: task record** | A JSON file per task: id, subject, status, owner, edges. | A whole-list snapshot, plus one goal with a phase and a round cap. |
| **How: dependencies** | `blockedBy` and `blocks` edges. A claim is refused until blockers finish. | None. List order is the only ordering. |
| **How: persistence** | One file per task, plus the largest issued id. A switch can replace todos. | Session events, replayed on load. Self-continuation is never stored. |
| **How: lifecycle** | `pending -> in_progress -> completed`, with a lock on claims. | Goal phases: active, paused, blocked, complete. Edits need a human. |

---

## Failure modes

- **Dependency cycle.** Two tasks can block each other. Keep the graph acyclic or add cycle checks.
- **Claim race.** Two agents can try the same task. Lock the claim path.
- **Orphaned in-progress task.** A worker can die after claiming. Clear ownership on worker exit.
- **Invalid record.** Hand-edited or old files may not match the schema. Parse safely and skip bad records.
- **Durable system disabled.** In-memory todos can still be lost. Use disk-backed tasks for work that must survive.

---

## Runnable

[`src/`](src/) carries 11 forward and adds:

- [`tasks.py`](src/tasks.py): a disk-backed `TaskStore`, claim gate, and `Task*` tools.
- [`test.py`](src/test.py): checks dependencies, claim gating, and a 10-agent claim race.
- [`demo.py`](src/demo.py): persists a three-task plan as JSON files.

```bash
python sections/12-task-system/src/test.py         # offline checks, no key
uv run python sections/12-task-system/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `utils/tasks.ts`, `Task.ts`, and the `Task*Tool/` directories.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/goal/goal/src/index.ts`, `packages/goal/goal-round-driver/README.md`, `packages/todo/tool-todo/README.md`,
  `docs/subsystems/goal.md`, `docs/persistence-catalog.md`.
- [learn-claude-code · s12_task_system](https://github.com/shareAI-lab/learn-claude-code): section framing.
