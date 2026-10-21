"""Protocols (section 17): typed request/reply over the section-16 channel.
Introduced in section 17, then carried forward unchanged.

The channel (section 16) only moves text; text alone has no contract. A protocol
is the agreed rule on top of it, and it is three small things:

  1. Typed variants. Every message carries a `type` field, so a handler
     dispatches on it and a reply is never mistaken for an unrelated request.
  2. A correlation id. `request_id` is set when the request goes out and echoed
     in the reply, so the sender knows which pending request a reply resolves.
  3. A small state machine. A request goes pending -> approved or rejected. A
     reply for an already resolved (or unknown) id is ignored, so duplicates and
     stray replies are harmless.

Two flows are mirror images of each other. In shutdown the lead requests and the
teammate confirms; in plan approval the teammate requests and the lead confirms.
Both ride the same `Protocol` tracker, just in opposite directions. Approval can
carry the permission mode the work runs under (section 3), so the verdict and the
mode it runs under travel together.

On top of the tracker, `run_teammate` is section 16's `serve_mailbox` extended
with the shutdown handshake: a spawned teammate now stops on a lead's request
instead of dying with its daemon thread. Initiating a stop is the lead's tool call
(StopTeammate); confirming it is harness-driven, done by the loop itself, not a
model tool (the reference confirms in the inbox dispatcher the same way).

ponytail: in-process pending dict, one tracker per agent; a cross-restart bus
would persist pending state, but the resolve logic stays the same.
"""
from __future__ import annotations

import time

from mailbox import _fold
from tools import Tool

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"

# Which reply variants answer each request, and the verdict each implies.
# None means a single response variant that carries the verdict in an `approved`
# field (the plan flow), rather than splitting it across two reply types.
_REPLIES = {
    "shutdown_request": {"shutdown_approved": APPROVED, "shutdown_rejected": REJECTED},
    "plan_approval_request": {"plan_approval_response": None},
}


class Protocol:
    """Per-agent request tracker over a Team channel (section 16). It records each
    outgoing request as pending and resolves the matching reply exactly once."""

    def __init__(self, team, me):
        self.team = team
        self.me = me
        self._n = 0
        self.pending = {}                         # request_id -> {"kind", "state"} (outbound)
        self.inbound = {}                         # request type -> the message to reply to

    def request(self, to, kind, **fields):
        """Send a typed request (`kind` is the wire `type` field), record it
        pending, and return its correlation id."""
        if kind not in _REPLIES:                  # only known request types have a reply contract
            raise ValueError(f"unknown request type: {kind!r}")
        self._n += 1
        rid = f"{self.me}-{self._n}"              # per-sender id: unique and deterministic
        self.pending[rid] = {"kind": kind, "state": PENDING}
        self.team.send(self.me, to, {"type": kind, "request_id": rid, **fields})
        return rid

    def reply(self, msg, kind, **fields):
        """Answer an inbound request by echoing its request_id back to the sender,
        so the reply correlates to exactly what it answers."""
        req = msg["content"]
        self.team.send(self.me, msg["from"], {"type": kind, "request_id": req["request_id"], **fields})

    def resolve(self, msg):
        """Apply an inbound reply to its pending request and return the new state.
        Returns None (a no-op) if the id is unknown, already resolved, or the reply
        type does not answer the recorded request. Idempotent on duplicates."""
        reply = msg["content"]
        req = self.pending.get(reply.get("request_id"))
        if not req or req["state"] != PENDING:    # unknown id or already resolved
            return None
        verdicts = _REPLIES[req["kind"]]
        if reply.get("type") not in verdicts:     # type-confusion guard: wrong reply kind
            return None
        state = verdicts[reply["type"]]
        if state is None:                         # single-response flow carries the bool
            state = APPROVED if reply.get("approved") else REJECTED
        req["state"] = state
        return state

    def note_inbound(self, msg):
        """Record an inbound typed request so a reply tool can answer it without
        the model passing request_ids around. Keyed by request type, latest wins."""
        content = msg.get("content")
        if isinstance(content, dict) and content.get("type") in _REPLIES:
            self.inbound[content["type"]] = msg


def protocol_tools(proto, lead="lead"):
    """The handshake initiations as tools the model calls, so gating a plan or
    stopping a teammate is the model's decision, not a script's. The worker submits
    a plan with ExitPlanMode; the lead answers with ApprovePlan and asks a worker to
    stop with StopTeammate. Replies correlate by request_id under the hood
    (note_inbound records the request). Confirming a shutdown is not a tool: the
    teammate's run_teammate loop replies automatically (harness-driven reception,
    like Claude Code's inbox dispatcher). Mirrors ExitPlanModeV2Tool and the stop
    path."""
    def exit_plan(a):                              # worker -> lead: gate the plan before editing
        proto.request(lead, "plan_approval_request", plan=a["plan"])
        return "plan submitted to the lead; awaiting approval before any edits"

    def approve_plan(a):                           # lead -> worker: answer the pending plan
        req = proto.inbound.get("plan_approval_request")
        if req is None:
            return "no plan is awaiting approval"
        fields = {"approved": bool(a.get("approved", True))}
        if a.get("permissionMode"):
            fields["permissionMode"] = a["permissionMode"]   # the verdict carries the mode (section 3)
        proto.reply(req, "plan_approval_response", **fields)
        return "plan approved" if fields["approved"] else "plan rejected"

    def stop_teammate(a):                          # lead -> worker: ask for a clean stop
        proto.request(a["name"], "shutdown_request", reason=a.get("reason", "done"))
        return f"asked {a['name']} to stop"

    return [
        Tool("ExitPlanMode", exit_plan, is_read_only=True,
             description="Submit your plan to the lead and wait for approval before editing.",
             input_schema={"type": "object", "properties": {"plan": {"type": "string"}},
                           "required": ["plan"]}),
        Tool("ApprovePlan", approve_plan, is_read_only=True,
             description="Approve or reject the plan a teammate submitted for review.",
             input_schema={"type": "object", "properties": {
                 "approved": {"type": "boolean"}, "feedback": {"type": "string"},
                 "permissionMode": {"type": "string"}}}),
        Tool("StopTeammate", stop_teammate, is_read_only=True,
             description="Ask a teammate to finish and stop cleanly, by name.",
             input_schema={"type": "object", "properties": {
                 "name": {"type": "string"}, "reason": {"type": "string"}},
                 "required": ["name"]}),
    ]


def _is_shutdown(msg):
    content = msg["content"]
    return isinstance(content, dict) and content.get("type") == "shutdown_request"


def run_teammate(team, me, lead, work, *, poll=0.05, max_idle_polls=None):
    """Section 16's serve_mailbox plus the shutdown handshake. Each pass drains the
    inbox: a shutdown_request is confirmed with shutdown_approved and ends the
    loop, so a teammate is stopped by a handshake, not by killing its daemon
    thread; chat messages fold into one prompt and run `work(prompt)`; an empty
    inbox polls again. The confirm is the loop's own doing, not a model tool call
    (harness-driven reception, like the reference's inbox dispatcher), and shutdown
    is checked before chat so peer traffic cannot starve a stop. Returns 'shutdown'
    when the handshake
    stops it, 'idle' if a bounded loop gives up first. Section 18 adds claiming
    board tasks when the inbox is empty."""
    proto = Protocol(team, me)
    idle = 0
    while True:
        inbox = team.drain(me)
        shutdown = next((m for m in inbox if _is_shutdown(m)), None)
        if shutdown is not None:
            proto.reply(shutdown, "shutdown_approved")      # confirm, then stop
            return "shutdown"
        chat = [m for m in inbox if isinstance(m["content"], str)]
        if chat:
            idle = 0
            work(_fold(chat))                               # one inner loop on the folded message
            continue
        idle += 1
        if max_idle_polls is not None and idle >= max_idle_polls:
            return "idle"
        time.sleep(poll)
