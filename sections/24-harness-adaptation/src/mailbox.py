"""Coordination (section 16): a team of named inboxes so several agents can run
at once and talk. Introduced in section 16, then carried forward unchanged.

A large job exceeds one context window, so it fans out across teammates. They
need three things a lone loop lacks: addressing (stable names), a channel (a way
to deliver a message the recipient will see), and escalation (a teammate hitting
a gated action must reach a human, not stall or self-approve).

Each agent owns an inbox: one JSON file per teammate under a shared team dir.
To talk you append to the recipient's inbox under a file lock, so concurrent
senders serialize; `to="*"` broadcasts. There is no broker. Delivery is the
recipient draining its own inbox and folding new messages into its next turn
(the poll-and-fold model: an agent never hard-blocks on a peer). Permission
requests ride the same channel: a teammate sends a permission_request, the lead
routes it to a human, and the verdict returns as a permission_response.

The lead does not hand-start teammates. It calls SpawnTeammate, and the harness
runs each teammate's serve_mailbox loop on a background thread (section 13). A
teammate pulls its own inbox and acts, so the script drives no one. There is no
graceful stop yet, the thread is a daemon; section 17 adds the shutdown
handshake, and section 18 adds pulling tasks off a shared board.

ponytail: one fcntl lock around the whole team (proper-lockfile's analog); split
to per-inbox locks only if a large roster makes that one lock a bottleneck.
"""
from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path

from tools import Tool


class Team:
    """A roster of named inboxes under `root`/inboxes, one JSON file each.
    The roster is closed: you can only address a registered teammate, which is
    also the trust boundary (a name becomes a path, so it must be known)."""

    def __init__(self, root, members):
        self.dir = Path(root) / "inboxes"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.members = list(members)
        for m in self.members:
            self._path(m).write_text("[]")          # empty inbox per teammate

    def send(self, frm, to, content):
        """Append a message to the recipient's inbox; to='*' broadcasts to every
        teammate but the sender. One lock serializes concurrent senders."""
        targets = [m for m in self.members if m != frm] if to == "*" else [self._check(to)]
        with self._lock():
            for t in targets:
                inbox = self._read(t)
                inbox.append({"from": self._check(frm), "to": t, "content": content})
                self._path(t).write_text(json.dumps(inbox))

    def drain(self, name):
        """Read and clear a teammate's inbox, oldest first. Delivery is the
        recipient draining its own inbox, so a peer never blocks waiting on it."""
        with self._lock():
            inbox = self._read(name)
            self._path(name).write_text("[]")
            return inbox

    # --- disk plumbing ---
    def _check(self, name):
        if name not in self.members:                # closed roster = the trust boundary
            raise ValueError(f"not a teammate: {name!r}")
        return name

    def _path(self, name):
        return self.dir / f"{self._check(name)}.json"

    def _read(self, name):
        return json.loads(self._path(name).read_text())

    @contextmanager
    def _lock(self):
        f = open(self.dir / ".lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX)           # blocks until the other sender releases
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()


def _fold(chat):
    """Render chat messages as one prompt block, the wire format a teammate reads.
    One definition so fold_inbox and serve_mailbox cannot drift."""
    return "\n".join(f"<message from={m['from']!r}>{m['content']}</message>" for m in chat)


def fold_inbox(messages, team, name):
    """Drain `name`'s inbox and surface chat (string) messages on its next turn,
    so a teammate folds peers' messages in between turns instead of blocking on
    them. Mirrors how background completions (section 13) prepend to the next
    turn. Returns every drained message (not just chat), so a structured message
    is never silently lost; the caller routes the rest (e.g. permission replies)."""
    drained = team.drain(name)
    chat = [m for m in drained if isinstance(m["content"], str)]
    if chat and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = _fold(chat) + "\n\n" + messages[-1]["content"]
    return drained


def serve_mailbox(team, me, work, *, poll=0.05, max_idle_polls=None):
    """A teammate's own loop, spawned on a background thread (section 13): pull the
    inbox, act, repeat. Each pass drains `me`'s inbox; chat messages fold into one
    prompt and run `work(prompt)` (one inner loop, section 1), and an empty inbox
    just sleeps and polls again. So a teammate reacts on its own thread instead of
    the script driving it. No graceful stop yet: the thread is a daemon that dies
    with the process. Section 17 adds the shutdown handshake; section 18 adds
    claiming tasks off a board when the inbox is empty. max_idle_polls bounds the
    idle wait so a demo or test ends; None runs until the process does. Returns
    'idle' when a bounded loop gives up."""
    idle = 0
    while True:
        chat = [m for m in team.drain(me) if isinstance(m["content"], str)]
        if chat:
            idle = 0
            work(_fold(chat))                           # one inner loop on the folded message
            continue
        idle += 1
        if max_idle_polls is not None and idle >= max_idle_polls:
            return "idle"
        time.sleep(poll)


def bubbling_approver(team, me, lead, human=None, timeout=0.0, poll=0.05):
    """An approver (section 3) for a teammate with no human in its own loop: a
    gated call escalates to the lead. The request and the verdict both travel the
    inbox channel; the teammate returns on what it reads back from its own inbox,
    so the answer is an inbox message, not a direct call.

    With `human`, the lead's approval UI answers inline (synchronous round-trip).
    Without it, the answer must arrive from elsewhere (a lead on another thread, a
    person on a chat platform), so the teammate polls its inbox up to `timeout`
    and then DENIES: an unanswered permission is a no, never a stall or a yes.
    Mirrors Hermes' clarify gateway (register / wait_for_response with timeout).
    ponytail: the wait drains chat messages too; route drained non-responses if
    teammates chat while a permission is pending."""
    def approve(name, args):
        team.send(me, lead, {"kind": "permission_request", "tool": name, "args": args})
        if human is not None:                       # the lead routes it to its approval UI
            team.send(lead, me, {"kind": "permission_response", "tool": name, "ok": human(name, args)})
        deadline = time.time() + timeout
        while True:
            responses = [m["content"] for m in team.drain(me)
                         if isinstance(m["content"], dict) and m["content"].get("kind") == "permission_response"]
            if responses:
                return bool(responses[-1]["ok"])
            if time.time() >= deadline:
                return False                        # nobody answered in time: default deny
            time.sleep(poll)
    return approve


def _send_tool(get_team, me):
    """The SendMessage tool, shared by message_tools and team_tools. `me` is bound
    by the harness, not the model, so a teammate cannot spoof another. get_team()
    returns the team at call time, so one implementation serves both a teammate
    with a fixed team and a lead whose team exists only after TeamCreate (None
    until then). Mirrors Claude Code's SendMessageTool."""
    def send(a):
        team = get_team()
        if team is None:
            return "no team yet: call TeamCreate with the member names first"
        team.send(me, a["to"], a["message"])
        return f"sent to {a['to']}"

    return Tool("SendMessage", send,
                description="Send a message to a teammate by name. Use 'lead' to reach the team lead.",
                input_schema={"type": "object",
                              "properties": {"to": {"type": "string"}, "message": {"type": "string"}},
                              "required": ["to", "message"]})


def message_tools(team, me):
    """SendMessage over an existing team: the model decides to message a teammate,
    and the harness delivers it over the inbox channel."""
    return [_send_tool(lambda: team, me)]


def team_tools(root, me, formed):
    """Forming the team is the model's decision, not the script's. TeamCreate
    materializes the inboxes under `root` into `formed`; the shared SendMessage
    stays inert until then, so the lead creates the team before it can talk to it.
    `formed` is a one-slot holder the harness reads back to hand the same Team to
    teammates that join (ponytail: an in-process stand-in for a team registry;
    back it with a roster file on disk to let a teammate in another process join)."""
    def create(a):
        members = list(dict.fromkeys([me, *a["members"]]))   # the creator joins its own team
        formed["team"] = Team(root, members)                 # the tool call brings the team into being
        return f"team created: {', '.join(members)}"

    return [Tool("TeamCreate", create,
                 description="Create the team with a list of member names. Call this before messaging.",
                 input_schema={"type": "object",
                               "properties": {"members": {"type": "array", "items": {"type": "string"}}},
                               "required": ["members"]}),
            _send_tool(lambda: formed["team"], me)]          # SendMessage, late-bound on the formed team


def teammate_tools(runtime, spawn_worker):
    """SpawnTeammate as a tool: the lead's model decides to add a teammate, and the
    harness starts its loop on a background thread (section 13's runtime).
    `spawn_worker(name)` is the app-supplied thunk that runs that teammate's loop
    (serve_mailbox here; an autonomy loop from section 18). Whether to spawn is the
    model's call, not the script's. Mirrors Claude Code spawning an
    InProcessTeammateTask."""
    def spawn(a):
        runtime.start(lambda: spawn_worker(a["name"]))
        return f"spawned teammate {a['name']}; it runs on its own thread and pulls its own work"

    return [Tool("SpawnTeammate", spawn, is_read_only=True,
                 description="Spawn a teammate by name; it runs on its own thread and pulls its own work.",
                 input_schema={"type": "object", "properties": {"name": {"type": "string"}},
                               "required": ["name"]})]
