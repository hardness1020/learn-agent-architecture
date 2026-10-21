"""Typed decisions on an edge (section 22).

run_graph evaluates an edge in code: a fixed node name, or a callable on the
state. Both cases need a branch you can already write down. This module adds
the third case, an edge that asks for a number when the branch needs judgment
but does not need a full model call.

ask(state) returns (p, confidence). p is the probability of the outcome the
edge cares about. confidence is how peaked the answer was, or None when the
answer type does not carry one. route() turns that pair into one of three
verdicts, and the band between the two thresholds is where a person goes.

The layer only narrows. Any missing key, timeout, transport error, or
malformed answer returns (None, None), which routes to "ask", so a broken
check falls back to the harness default instead of granting a branch.
"""
from __future__ import annotations

import json
import os
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def route(p, conf, allow_below=0.10, deny_above=0.90, conf_floor=0.45):
    """Turn (probability, confidence) into "allow", "ask", or "deny".

    Two thresholds, not one. Everything between them is the abstain band:
    the harness stops and asks rather than guessing. A low confidence may
    only make the verdict more cautious, never less."""
    if p is None:
        return "ask"                               # no layer, or the call failed
    if conf is not None and conf < conf_floor:
        return "ask"                               # too flat to act on either way
    if p >= deny_above:
        return "deny"
    if p <= allow_below:
        return "allow"
    return "ask"                                   # the band: this is where a person goes


def decision_edge(ask, on, **thresholds):
    """An edge that routes on a probability. Plug into edges[name] as usual.

    on maps each verdict to what runs next, either a node name or a callable
    on the state, the same two forms run_graph already accepts."""
    def edge(state):
        p, conf = ask(state)
        nxt = on[route(p, conf, **thresholds)]
        return nxt(state) if callable(nxt) else nxt

    return edge


def fixture_asker(table, key="task"):
    """Recorded answers, keyed by one state field. Offline and deterministic.

    A fixture is what makes the routing testable without a key or a network.
    It pins the mechanism. It says nothing about whether the numbers are good."""
    def ask(state):
        return table.get(state.get(key), (None, None))

    return ask


def typesafe_asker(instructions, criteria, harmful, model="jev-latest", timeout=8.0):
    """One typed question against a decision model, over stdlib http.

    Sends a choice question and reads back the probability of the harmful
    option plus the answer's confidence. Every failure path returns
    (None, None) on purpose, so the edge degrades to the harness default."""
    def ask(state):
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            return (None, None)
        body = json.dumps({"state": state, "model": model,
                           "questions": {"q": {"type": "choice",
                                               "instructions": instructions,
                                               "criteria": criteria}}}).encode()
        req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                answer = json.load(r)["answers"]["q"]
            return (answer["probabilities"][harmful], answer["confidence"])
        except Exception:                          # transport, status, or shape
            return (None, None)                    # narrow only: never grants a branch

    return ask
