# 16 · Coordination

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> A lead forms a team sized to the task, spawns teammates on their own threads, and they talk over a shared inbox.

One agent has one context window and one active line of work. Large jobs often need several agents working at once.

A subagent can handle a focused task, but a one-shot subagent is hard to steer after it starts.

Every extra agent costs tokens, and two agents can edit the same file in different directions.
So the first decision is the shape of the team: how many agents, whether they share one context, and who tells whom what to do.

Coordinated agents need a way to spawn each other, stable names, inboxes to talk, and a way to send permission requests back to a human.

Coordination must:

1. Give agents stable addresses.
2. Let the lead size and form the team for the task.
3. Let the lead spawn each teammate onto its own thread.
4. Let each teammate pull its inbox and act without the script driving it.
5. Bubble gated actions to a human approver.

Without this layer, large work either stays serial or splits into workers that cannot collaborate.

---

## Mechanism

![Mechanism diagram](assets/16-coordination.png)

Each agent owns an inbox. Sending a message means writing to the recipient's inbox. Delivery happens when the recipient drains its inbox.

The team's size and names are decided at run time by the lead's model, not hard-coded in the script.
The lead calls `TeamCreate` to form the team for the task, then spawns each member.

The lead does not hand-start teammates. It calls `SpawnTeammate`, and the harness runs the teammate's loop on a background thread (section 13).
The teammate then pulls its own inbox and acts, so the script drives no one.

There is no central broker in the demo. There is a shared convention for names, inbox paths, and message shape.

- Each agent owns one inbox.
- A message has a sender, recipient, and content.
- The lead calls `TeamCreate` to size and form the roster; `SpawnTeammate` then starts each member.
- The lead spawns a teammate with `SpawnTeammate`; that teammate runs on its own thread.
- `to="*"` broadcasts to every teammate except the sender.
- Senders write and return. They do not block waiting for a reply.
- A teammate pulls its inbox each poll and folds new messages into its next turn.
- Permission requests use the same channel.

### New: forming the team

`TeamCreate` is a tool the lead calls to size and form the roster. It fills a one-slot holder the harness reads back when it spawns each member:

```python
def team_tools(root, me, formed):                      # src/mailbox.py
    def create(a):
        members = list(dict.fromkeys([me, *a["members"]]))   # the lead joins its own team
        formed["team"] = Team(root, members)                 # the tool call sizes and forms the team
        return f"team created: {', '.join(members)}"
    ...                                                # SendMessage stays inert until the team exists
```

- The script fixes neither the size nor the names; the lead picks both from the task.
- `SendMessage` is inert until `TeamCreate` runs, so the lead forms the team before it can talk to it.
- `formed` is a one-slot holder (ponytail: an in-process stand-in for a team registry; back it with a roster file to let a teammate in another process join).

### New: spawning a teammate

`SpawnTeammate` is a tool the lead's model calls. The harness starts the teammate's loop on the section-13 runtime, on its own thread:

```python
def teammate_tools(runtime, spawn_worker):             # src/mailbox.py
    def spawn(a):
        runtime.start(lambda: spawn_worker(a["name"]))  # section-13 thread runs the teammate's loop
        return f"spawned teammate {a['name']}; it runs on its own thread and pulls its own work"
    return [Tool("SpawnTeammate", spawn, is_read_only=True, ...)]
```

The teammate's loop is `serve_mailbox`: pull the inbox, act, repeat. It runs on the spawned thread, so the teammate reacts on its own, not on a script:

```python
def serve_mailbox(team, me, work, *, poll=0.05, max_idle_polls=None):   # src/mailbox.py
    while True:
        chat = [m for m in team.drain(me) if isinstance(m["content"], str)]
        if chat:                                        # a message to act on
            folded = "\n".join(f"<message from={m['from']!r}>{m['content']}</message>" for m in chat)
            work(folded)                                # one inner loop (section 1) on the message
            continue
        time.sleep(poll)                                # empty: poll again
```

- `spawn_worker(name)` is the app's thunk; it runs one `serve_mailbox` loop for that teammate.
- The teammate consumes messages as it drains, so a message is delivered once.
- There is no graceful stop yet. The thread is a daemon that dies with the process. Section 17 adds the shutdown handshake.
- `max_idle_polls` bounds the idle wait so a demo or test ends; a real teammate polls until the process stops.

### The inbox and the permission channel

Agents with separate contexts have two ways to talk, the same two that processes have.
With shared memory, everyone reads and writes one place and sees the same state.
With message passing, a sender addresses a copy to one receiver, and the two share nothing.
Three channels carry those two. Tool-call arguments go one way and have no reply path. Files survive a restart but need locks.
A message bus adds addresses and ordering, and survives a restart only if it writes to disk.
An inbox here is a locked file, so it is message passing over a shared filesystem.
Team memory (section 9) and the task board (section 18) are the shared-memory side.
Most teams want both: messages to hand out work, shared memory for facts that outlive a message.

`mailbox.py` implements a `Team` of named inboxes:

```python
def send(self, frm, to, content):                      # src/mailbox.py
    targets = [m for m in self.members if m != frm] if to == "*" else [self._check(to)]
    with self._lock():                                 # serialize concurrent senders
        for t in targets:
            inbox = self._read(t)
            inbox.append({"from": frm, "to": t, "content": content})
            self._path(t).write_text(json.dumps(inbox))
```

- `_check` rejects unknown names before they become paths.
- The lock serializes read-modify-write, so concurrent senders do not drop messages.
- `drain` reads and clears one inbox.

Permission bubbling is an approver implementation. It moves a gated call to a human over the same channel:

```python
def bubbling_approver(team, me, lead, human=None, timeout=0.0, poll=0.05):
    def approve(name, args):                            # approver for an agent with no human UI
        team.send(me, lead, {"kind": "permission_request", "tool": name, "args": args})
        if human is not None:                           # the lead routes it to its approval UI
            team.send(lead, me, {"kind": "permission_response", "tool": name, "ok": human(name, args)})
        deadline = time.time() + timeout
        while True:
            resp = [m["content"] for m in team.drain(me)
                    if isinstance(m["content"], dict) and m["content"].get("kind") == "permission_response"]
            if resp:
                return bool(resp[-1]["ok"])
            if time.time() >= deadline:
                return False                            # nobody answered in time: default deny
            time.sleep(poll)
    return approve
```

1. A teammate hits a gated tool call, but its own loop has no human at the keyboard.
2. The approver sends a `permission_request` to the lead's inbox.
3. The lead routes it to its approval UI (the `human` callback here).
4. The verdict returns as a `permission_response` in the teammate's inbox.
5. The teammate reads that response and returns allow or deny to the gate.

The gate still calls `approver(name, args)` and does not change. The answer arrives as an inbox message, not a direct call, so escalation reuses the same channel.

Without `human`, the answer must come from elsewhere (a lead on another thread, a person on a chat platform).
The approver polls its inbox up to `timeout` and then denies: an unanswered permission is a no, never a stall or a yes.
This mirrors Hermes' clarify gateway, where `wait_for_response` blocks the agent thread until a chat adapter answers or the timeout fires.

### How it integrates

The demo runs one main agent. The lead takes one step, and the teammate runs itself:

```python
def spawn_worker(name, formed, model):                 # src/demo.py, module level
    team = formed["team"]                              # whatever the lead formed with TeamCreate
    ...                                                 # build the teammate's tools
    return mailbox.serve_mailbox(team, name, work)      # the teammate pulls its own inbox

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
```

- The only scripted input is the lead's goal. The lead sizes the team with `TeamCreate`, spawns each with `SpawnTeammate`, and delegates with `SendMessage`.
- `demo()` runs one `run_turn`, the lead's. The teammate's own `run_turn` lives in `spawn_worker`, reached only through the spawn tool.
- Each teammate runs `serve_mailbox` on a section-13 thread: it pulls its inbox, works, and replies. The number of replies is the lead's choice; the main process only waits.
- `loop.py` stays generic. Folding and the pull loop are coordination, done in this wrapper, not inside `run_turn`.
- The permission gate does not change; a gated call still bubbles to the lead.

### Further reading

None of this is in `src/`. It comes from ai-agent-book and published multi-agent research, and is not confirmed of the systems in the table.

**When a team beats one agent.** Add a second agent only when it brings back something the first one could not see.
A test result, a screenshot, a page it fetched, an answer from a running system. That is new information.
An agent that reads the same text again and votes brings no new information. It only spends tokens.

Two published results set the price of getting this wrong. Tran and Kiela gave a single agent and a team the same thinking token budget.
On the tasks they measured, the single agent kept up. Anthropic reports its research team using about fifteen times the tokens of one chat turn.
A team that expensive has to bring something back.

**Shared or isolated context.** Two agents either share one history or keep separate ones:

- **Shared.** The next agent inherits everything, so nothing has to be packed and no fact goes missing.
  The cost is that one agent runs at a time, and one window holds the whole team's history.
- **Isolated.** Each agent gets its own window and has to say what it needs. Agents run at the same time, and one agent's confusion stops at its own window.
  The cost is that every handoff has to be written down.

Pick shared when there are few subtasks, the history fits one window, and the steps run in order anyway. Isolate otherwise.
This repo isolates: a subagent starts empty (section 6), and a teammate reads only its inbox.

**Three topologies.** Isolated agents still have to know who talks to whom. There are three shapes:

- **Peer.** Agents of equal standing message each other. Review and cross-checking fit here.
- **Manager.** One lead splits the work, hands it out, and merges what comes back. Children return summaries, not their histories.
- **Decentralized.** No lead. Each agent picks who gets the work next.

This section builds a manager. The lead plans for everyone, so a bad split stays bad and no worker can fix it.
That is the argument for giving the lead the strongest model and the workers cheaper ones.

**How decentralized teams route work.** With no lead, the work still has to find the next agent. Three published designs, three routes:

- **MetaGPT** posts every message to a pool. Each role subscribes to the message types it handles, so a sender never names a receiver.
- **AutoGen** group chat keeps one transcript and lets a central selector pick who speaks next. If the selector keeps picking the same two agents, the chat livelocks.
- **OpenAI Swarm** makes each handoff a tool call and caps how many times work can change hands, so a chain of handoffs ends.

**Four regions of the file tree.** Agents find each other by name. They find state by path. The book splits the tree into four regions:

- **Private scratchpad.** One agent's drafts. Nobody else reads it, so nothing needs coordinating.
- **Shared workspace.** The repo, the task board, and team memory. Every teammate writes here, so this is where conflicts happen.
  It needs locks, or a worktree per agent (section 15).
- **External mounts.** Data the team did not make, such as a checkout or a dataset. A write here changes something outside the team.
- **Read-only built-ins.** Skills, prompts, and tool definitions (sections 7 and 2). They do not change during the run, so every agent sees the same copy.

Put state in the wrong region and it comes back as a coordination bug. Two agents editing one file means that file sat in the shared workspace.
A fact sent three times means it should have gone to team memory.

**What a handoff carries.** A teammate cannot see the lead's chat, so "fix the failing test" gives it nothing to act on. A handoff packet carries three things:

1. The task, with acceptance criteria the receiver can check on its own.
2. The facts already confirmed and the constraints that hold, so the receiver does not look them up again or break them.
3. Paths to the files, logs, and branches.

The sender's raw history stays out. It is long, it is full of dead ends, and it makes the receiver read the sender's mistakes.

Shared-context handoff is the other option, and it skips the packet. One agent hands control to another and the whole history goes along, so nothing is left behind.
The book shows this with a tool that moves control between roles. That is the author's own experiment, so treat it as one source.
The cost is that only one agent holds control, so nothing runs at the same time. A packet takes work to write and buys work that runs in parallel.

> **Next:** A teammate here is a daemon with no graceful stop, and it only reacts to messages.
> Section 17 adds the shutdown handshake so the lead can end a teammate cleanly.
> Section 18 adds a shared task board, so an idle teammate claims its own work instead of waiting to be messaged.

---

## Per system

How one design spawns cooperating agents and spreads work across them.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Peers talk directly. File inboxes cross processes. | Children pause and interrupt from any surface. | A script fans out many children under hard caps. |
| **Cons** | Polling and lock cost. Memory inboxes die with the process. | No peer inboxes. A clarify blocks its thread. | Children cannot talk. A send gets no reply. |
| **Why** | Peers need inboxes, plus a route to a human approver. | Coordination stays parent to child. | Coordination is ownership. Each child has one parent. |
| **How: teammates** | In-process or remote, each runs its own loop. | Delegated children on threads, with a pause flag. | A model-written script spawns them; some stay resident. |
| **How: channel** | SendMessage writes to inboxes and can broadcast. | Completion queue plus gateway calls. | Parent to child only. The child answers with a report tool. |
| **How: shared memory** | Team task list and a team memory directory. | Shared session DB, plus lineage markers. | The parent's directory. A fork also copies its finished turns. |
| **How: permission bubbling** | Remote requests become local approval prompts. | Clarify goes to chat; children auto-deny or approve. | A request walks up the parent chain. |

---

## Failure modes

- **Lost message race.** Two senders write one inbox at once. Lock read-modify-write.
- **Peer deadlock.** Agents wait on each other. Queue messages and drain between turns instead of blocking sends.
- **Permission stalls.** A teammate has no human UI. Bubble the request to the lead.
- **Spawn before create.** The lead spawns or messages before `TeamCreate`, so there is no roster. Keep both inert until the team exists.
- **Orphaned teammate.** A spawned teammate keeps polling after its work is done. Bound the idle wait, or stop it with the section-17 handshake.
- **Vague cross-agent message.** A teammate cannot see the lead's chat. Send a packet: task, acceptance criteria, confirmed facts, artifact paths.
- **Chat used as memory.** Durable shared facts belong in team memory.
- **Byzantine teammate.** A failed agent does not crash. It returns a wrong answer and sounds sure of it.
  Retrying it or voting over the same evidence gets the same answer back. Only a check against something outside the model catches it.
- **Lost update on a shared file.** Two agents read one file and both write it back. The first write is gone.
  Lock the write, or save a version number and retry when it does not match.
- **Semantic conflict.** Both writes apply cleanly and the result is still broken. One agent renamed a function while the other added calls to the old name.
  Split the work so no two agents own the same thing, or merge at one point.
- **Error cascade.** One agent gets a fact wrong. The next agent repeats it, the one after that repeats it again, and by then it reads as confirmed.
  A reviewer that sees only the conclusions finds them consistent. Have someone check the raw evidence, and not the agent that produced it.

---

## Runnable

[`src/`](src/) carries 15 forward and adds:

- [`mailbox.py`](src/mailbox.py): named inboxes with locking, folding, the `serve_mailbox` loop, bubbling with timeout and default deny, and the team tools.
- [`test.py`](src/test.py): checks addressing, broadcast, concurrent send, folding, bubbling (inline, async, and timeout-deny), the mailbox loop, and the team tools.
- [`demo.py`](src/demo.py): the lead takes one step (`TeamCreate`, `SpawnTeammate`, `SendMessage`); each teammate pulls its inbox, runs a gated shell task, and reports back.

The loop and subagent path are unchanged. Coordination wraps a turn by spawning teammates, draining inboxes, and passing an approver.

```bash
python sections/16-coordination/src/test.py         # offline checks, no key
uv run python sections/16-coordination/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code tools and inboxes](https://github.com/yasasbanukaofficial/claude-code):
  `tools/SendMessageTool/`, `tools/TeamCreateTool/`, `utils/mailbox.ts`, `utils/teammateMailbox.ts`.
- [Claude Code teammates](https://github.com/yasasbanukaofficial/claude-code):
  `tasks/InProcessTeammateTask/`, `tasks/RemoteAgentTask/`, `remote/remotePermissionBridge.ts`, `memdir/teamMemPaths.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent): `tools/delegate_tool.py`, `tools/async_delegation.py`, `tools/clarify_gateway.py`, `tools/interrupt.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/subsystems/workflow.md`, `docs/subsystems/subagent.md`, `docs/subsystems/core.md`,
  `packages/workflow/workflow-worker-thread/README.md`, `packages/subagent/tool-subagent-report/README.md`.
- [learn-claude-code · s15_agent_teams](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter10.md` (多 Agent 协作), Chinese original canonical.
  Context sharing, topology taxonomy, filesystem regions, handoff packets. The role-transfer demo is the author's own experiment.
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* ([arXiv:2503.13657](https://arxiv.org/abs/2503.13657)): the MAST taxonomy and the Byzantine framing.
- Tran, Kiela, *Single-Agent LLMs Outperform Multi-Agent Systems Under Equal Thinking Token Budgets* ([arXiv:2604.02460](https://arxiv.org/abs/2604.02460)).
- Erdogan et al., *Plan-and-Act* ([arXiv:2503.09572](https://arxiv.org/abs/2503.09572)): planner quality bounds the run.
- Anthropic, [*How we built our multi-agent research system*](https://www.anthropic.com/engineering/multi-agent-research-system): token cost of a research team.
- [MetaGPT](https://arxiv.org/abs/2308.00352), [AutoGen](https://arxiv.org/abs/2308.08155), [OpenAI Swarm](https://github.com/openai/swarm): decentralized routing and handoff caps.
