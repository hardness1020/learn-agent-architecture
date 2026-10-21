"""Section 24 demo: one adaptation step at k = 1 on section 23's refund episode.

The executor is run_turn (section 1) inside section 23's environment, with the
configuration's always-on text as its system prompt. The schedule is fixed
code: a reference run, two editors, one probe per candidate, a composer, and
the final run. Every judgment in it is the model's: each editor and the
composer is one model call that returns a whole configuration or KEEP_BASE.

No session in the step sees the rubric. After the step, section 23's grade
scores the delivered run only, and prints UNGRADED if its grader broke.

The step runs the executor four times, so this demo bends the one run_turn
per demo() rule on purpose: the step itself is the mechanism.

    uv run python sections/24-harness-adaptation/src/demo.py   (needs ANTHROPIC_API_KEY; see root README)
"""
import json
import os

from anthropic import Anthropic
from dotenv import load_dotenv

from adapt import KEEP_BASE, SURFACE, diff, step
from evaluation import Env, grade, run_episode, scripted_user
from loop import Session, run_turn
from permissions import DEFAULT
from tools import Registry, Tool

load_dotenv(override=True)

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

BASE = {"AGENTS.md": "You are a support agent for an online store. Keep replies to one or two sentences."}

SCOPES = {"invariant": "how the agent works: procedure, tool use, when to ask, when it is done",
          "domain": "what a practitioner knows: store policy and domain facts"}

ORDERS = {"A17": {"status": "delivered", "total": "40.00"},
          "B92": {"status": "shipped", "total": "12.50"}}

ID_SCHEMA = {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}


def get_order(state, order_id):
    o = state["orders"][order_id]
    return f"{order_id} {o['status']} {o['total']}"


def refund(state, order_id):
    o = state["orders"][order_id]
    o["status"] = "refunded"
    state["balance"] = round(state["balance"] + float(o["total"]), 2)
    return o["total"]


TASK = {                                               # section 23's record; only grade() reads the rubric
    "id": "refund-A17",
    "user": scripted_user(["I want a refund for something I bought.",
                           "It is order A17.", "Thanks, that is all."]),
    "checks": [("refunded", lambda s: s["orders"]["A17"]["status"] == "refunded"),
               ("credited", lambda s: s["balance"] == 40.0)],
    "must_say": ["40.00"],
    "veto": [("refunded an order the customer never named",
              lambda r: r["state"]["orders"]["B92"]["status"] == "refunded")],
}


def log(run):
    """A run as the improver reads it: what was said, which calls ran, the state it left. No verdict."""
    talk = [f"user: {said}\nagent: {reply.strip()}" for said, reply in run["transcript"]]
    return "\n".join(talk + [f"tool calls (name, legal): {run['calls']}", f"final state: {run['state']}"])


def parse(text):
    """A reply's configuration: one JSON object of path to text, or KEEP_BASE. Anything else fails check()."""
    if KEEP_BASE in text and "{" not in text:
        return KEEP_BASE
    try:
        return json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        return None


RULES = ("There is no rubric and no score. A run shows how the agent behaves, not whether it was right.\n"
         f"Allowed paths: {', '.join(SURFACE['paths'])}. AGENTS.md loads every session.\n"
         "Reply with one line per edit citing the moment in a run behind it, then the whole configuration "
         "as one JSON object of path to text. Or reply KEEP_BASE.")


def demo():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("24 adaptation: set ANTHROPIC_API_KEY to run the live demo (offline checks: test.py)")
        return

    client = Anthropic(base_url=os.environ.get("ANTHROPIC_BASE_URL") or None)

    def model(messages, registry, system):
        kwargs = {"system": system} if system else {}
        return client.messages.create(model=MODEL, messages=messages,
                                      tools=registry.schemas(), max_tokens=512, **kwargs)

    def ask(prompt, feedback):
        if feedback:                                   # the validator's message rides into the one retry
            prompt += f"\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED: {feedback}"
        reply = client.messages.create(model=MODEL, max_tokens=2048,
                                       messages=[{"role": "user", "content": prompt}])
        return parse(reply.content[0].text)

    def execute(config):
        """One executor run: a fresh environment, the same task, this configuration as the system prompt."""
        env = Env(initial={"orders": ORDERS, "balance": 0.0},
                  tools={"get_order": get_order, "refund": refund})
        reg = Registry()
        reg.register(Tool(name="get_order", run=lambda a: env.call("get_order", **a),
                          description="Read one order: status and total.",
                          input_schema=ID_SCHEMA, is_read_only=True))
        reg.register(Tool(name="refund", run=lambda a: env.call("refund", **a),
                          description="Refund one order to the customer's balance.", input_schema=ID_SCHEMA))
        messages = []

        def agent(said, _env):
            messages.append({"role": "user", "content": said})
            return run_turn(messages, model, reg, Session(mode=DEFAULT, allow_rules={"refund"}),   # a sandbox: pre-approved
                            prompt=lambda r, s: config["AGENTS.md"])

        return run_episode(env, TASK, agent)

    def editor(scope):
        def edit(config, evidence, feedback):
            return ask(f"You edit a support agent's configuration. Your scope is {SCOPES[scope]}. Leave the rest alone.\n"
                       f"{RULES}\n\nConfiguration:\n{json.dumps(config, indent=2)}\n\n"
                       "Reference run:\n" + "\n\n".join(log(r) for r in evidence), feedback)
        return edit

    def composer(config, evidence, pool, feedback):
        candidates = "\n\n".join(f"## {c['label']}\nDiff:\n{c['diff'] or '(no change)'}\nProbe:\n{log(c['probe'])}"
                                 for c in pool)
        return ask("You compose a support agent's next configuration from two edited copies, each run once on the same task.\n"
                   "Merge the edits the probes support, adopt one copy whole, or keep the current configuration.\n"
                   "Each copy ran once, so one good or bad probe is weak evidence.\n"
                   f"{RULES}\n\nCurrent configuration:\n{json.dumps(config, indent=2)}\n\n"
                   "Reference run:\n" + "\n\n".join(log(r) for r in evidence) + f"\n\nCandidates:\n{candidates}", feedback)

    final = step(BASE, execute, editor("invariant"), editor("domain"), composer)
    v = grade(TASK, final["run"])                      # the judge runs after the step; nothing it writes flows back

    print("24 adaptation: composed", "KEEP_BASE" if final["kept_base"] else
          "a fallback" if final["fell_back"] else "an edited configuration")
    print(diff(BASE, final["config"]) or "24 adaptation: no change")
    for said, reply in final["run"]["transcript"]:
        print(f"24 adaptation: user  | {said}")
        print(f"24 adaptation: agent | {reply.strip()}")
    print("24 adaptation: state checks:", v["checks"], "· told:", v["told"])
    print("24 adaptation:", "UNGRADED" if not v["graded"] else "PASS" if v["passed"] else "FAIL")


if __name__ == "__main__":
    demo()
