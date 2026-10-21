"""Task system (section 12): durable work records on disk, with a status state
machine, blockedBy/blocks dependency edges, and a lock-serialized claim.

Introduced in section 12, then carried forward unchanged.

Each task is one JSON file under a task dir. create/get/update/list are CRUD;
claim() refuses a task whose blockers are not all completed, and takes an
exclusive file lock so two agents (section 16) cannot both win the same task.
IDs are sequential, tracked in .highwatermark so a deleted id is never reused.
Mirrors Claude Code's utils/tasks.ts (TaskSchema, createTask/claimTask,
getTaskPath, .highwatermark, proper-lockfile).
"""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path

from tools import Tool


class TaskStore:
    """The durable task graph: one JSON file per task under `root`."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, subject, blocked_by=()):
        tid = self._next_id()
        task = {"id": tid, "subject": subject, "status": "pending",
                "owner": None, "blockedBy": list(blocked_by), "blocks": []}
        self._write(task)
        for b in blocked_by:                           # keep the reverse edge in sync
            dep = self.get(b)
            if dep is not None:
                dep["blocks"].append(tid)
                self._write(dep)
        return task

    def update(self, tid, status=None):
        task = self.get(tid)
        if task is None:
            return None
        if status is not None:
            task["status"] = status                    # soft: any transition the caller asks for
        self._write(task)
        return task

    def claim(self, tid, owner):
        """Claim a task for `owner` iff it is unowned and every blocker is
        completed. Serialized by an exclusive lock so a claim race has one winner."""
        with self._lock():
            task = self.get(tid)
            if task is None:
                return {"ok": False, "reason": "not_found"}
            if task["owner"] is not None:
                return {"ok": False, "reason": "already_claimed"}
            unmet = [b for b in task["blockedBy"]
                     if (self.get(b) or {}).get("status") != "completed"]
            if unmet:
                return {"ok": False, "reason": "blocked"}
            task["owner"], task["status"] = owner, "in_progress"
            self._write(task)
            return {"ok": True, "task": task}

    def get(self, tid):
        return self._read(self._path(tid))

    def list(self):
        tasks = (self._read(p) for p in self.root.glob("*.json"))
        return sorted((t for t in tasks if t is not None), key=lambda t: t["id"])

    # --- disk plumbing ---
    def _path(self, tid):
        return self.root / f"{tid}.json"

    def _write(self, task):
        self._path(task["id"]).write_text(json.dumps(task))

    def _read(self, path):
        try:
            return json.loads(Path(path).read_text())
        except (OSError, ValueError):                   # missing or hand-corrupted: skip, don't crash
            return None

    def _next_id(self):
        hwm = self.root / ".highwatermark"
        n = (int(hwm.read_text()) + 1) if hwm.exists() else 1
        hwm.write_text(str(n))                          # ponytail: single-writer creates; claim() is the locked path
        return n

    @contextmanager
    def _lock(self):
        f = open(self.root / ".lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX)               # blocks until the other claimer releases
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()


def task_tools(store: TaskStore):
    """The four LLM-facing tools, thin wrappers over the store (Claude Code's
    TaskCreate / TaskUpdate / TaskGet / TaskList)."""
    def create(a):
        t = store.create(a["subject"], a.get("blockedBy", []))
        return f"created task {t['id']}"

    def update(a):
        t = store.update(a["id"], status=a.get("status"))
        return f"task {a['id']} -> {t['status']}" if t else "no such task"

    def get(a):
        t = store.get(a["id"])
        return json.dumps(t) if t else "no such task"

    def lst(_a):
        return json.dumps([{"id": t["id"], "subject": t["subject"], "status": t["status"]}
                           for t in store.list()])

    return [
        Tool("TaskCreate", create, description="Create a durable task. May depend on others.",
             input_schema={"type": "object", "properties": {
                 "subject": {"type": "string"},
                 "blockedBy": {"type": "array", "items": {"type": "integer"}}},
                 "required": ["subject"]}),
        Tool("TaskUpdate", update, description="Set a task's status (pending|in_progress|completed).",
             input_schema={"type": "object", "properties": {
                 "id": {"type": "integer"}, "status": {"type": "string"}},
                 "required": ["id", "status"]}),
        Tool("TaskGet", get, description="Read one task by id.", is_read_only=True,
             input_schema={"type": "object", "properties": {"id": {"type": "integer"}},
                           "required": ["id"]}),
        Tool("TaskList", lst, description="List all tasks.", is_read_only=True),
    ]
