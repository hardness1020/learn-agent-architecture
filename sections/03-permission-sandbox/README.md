# 3 · Permission & sandbox

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Check each action before it reaches the system.

The model can ask to run any enabled tool. The permission layer decides whether that call may run.

A tool runtime without permissions is close to an unattended remote shell.

A bad tool call can delete files, leak secrets, or push the wrong code. Trusting the model is not a safety boundary. Code must check the request before execution.

The reason is simple. The model reads text that other people wrote. A web page, an issue comment, or a file in the repo can carry
instructions aimed at the agent. Three capabilities decide what those instructions can do. The agent can read private data.
The agent takes in untrusted content. The agent can send data out. Any two of the three are survivable. All three at once mean
injected text can tell the agent to open a secret and post it somewhere. That combination is called the lethal trifecta.

Persistent memory makes it worse. If the injected instruction lands in a memory file (section 9), the next session reads it back.
One injection then keeps working long after the session that carried it ended.

The gate cannot take those three capabilities away. An agent that reads nothing and reaches nothing cannot work. So the gate does
two other things. It puts a decision in front of calls that would complete the trio. It puts a sandbox behind the calls it allows.

The permission layer must:

1. Inspect each tool call before it runs.
2. Decide `allow`, `ask`, or `deny`.
3. Ask a human when a risky call is not pre-approved.
4. Limit damage when a call does run.

Without this layer, one bad tool call can cause an irreversible side effect.

---

## Mechanism

![Mechanism diagram](assets/03-permission-and-sandbox.png)

A pure function makes the permission decision. It reads the tool, the current mode, and any allow rules. It returns one of three values:

- `allow`: run the tool.
- `ask`: pause and ask a human.
- `deny`: do not run the tool.

The mode changes the default behavior. For example, plan mode allows read-only tools but denies edits until the plan is approved.

### New: the gate

`decide()` is the whole permission decision:

```python
def decide(tool, mode, allow_rules) -> str:      # src/permissions.py (new)
    if mode == BYPASS:                            # operator opted out
        return "allow"
    if mode == PLAN:                              # exploring, not acting yet
        if tool.is_read_only:           return "allow"
        if tool.name == "ExitPlanMode": return "ask"     # approval handshake (section 5)
        return "deny"                             # no side effects until approved
    if tool.is_read_only or tool.name in allow_rules:
        return "allow"
    if mode == ACCEPT_EDITS and tool.is_edit:
        return "allow"                            # a class of work pre-approved
    return "ask"                                  # default: when unsure, ask
```

The function has no I/O. That makes it easy to test mode by mode.

### How it integrates

The gate runs inside `_dispatch`, just before `run_tool`:

```python
def _dispatch(block, registry, mode, allow_rules, approver):   # src/loop.py
    ...                                                  # resolve tool (section 2)
    decision = decide(tool, mode, allow_rules)           # 3 · the gate, the new line
    if decision == "deny":
        return res(f"{name} not allowed in {mode} mode")
    if decision == "ask" and not approver(name, block.input):
        return res(f"{name} denied by user")
    return res(run_tool(tool, block.input))              # only now does it run
```

- The loop body is unchanged from sections 1 and 2.
- Only `_dispatch` gains the gate.
- `deny` and unapproved `ask` never reach `run_tool`.
- The denial still returns as a `tool_result`, so the model sees what happened and can adapt.
- `approver` defaults to `False`, so `ask` means no unless the human approves.

The key invariant stays intact: every tool call produces a result message, even when the real action did not run.

Real systems add rule priority, remembered approvals, and sandboxed execution. Those are extensions of the same gate.

### Further reading

None of this is in `src/`. It comes from ai-agent-book, and is not confirmed of the systems in the table.

**Checking what a command does, not how it is spelled.** The agent asks to run a shell command. `decide()` sees one tool name,
so the real decision is about the command string. A deny list of strings is the usual answer, and it fails. `rm -rf /` is easy to catch.
These get through:

- `find . -exec rm {} \;` puts the delete inside a flag.
- `$(echo rm) -rf /` builds the word `rm` while the shell runs.
- `curl -o /etc/crontab` writes a file without naming a write command.

The fix is a parser that reads structure instead of text. It splits the command into programs and arguments. It knows which flags take
a value, so it can tell an argument from a flag. Then it asks what each program will do. `-exec` carries its own command, so that command
gets checked too. `-o` names a file to write, so that path gets checked as a write.

The cost is that the parser needs rules per program. A program it does not know is a program it cannot read, so the sandbox still sits behind it.

**Blocking destructive shortcuts that would still pass.** There are two ways to fix a broken table, and both end with a working table.
One migrates it. The other drops it and rebuilds it from scratch. A result check (section 21) passes both, because it only looks at the end state.

The fix is to gate the route, not only the destination. Drop and rebuild stays blocked even when the rebuilt table would be correct.
The cost is that a rebuild which really is the right fix now needs a human to approve it.

**What the sandbox limits.** The gate can be wrong. The sandbox is what keeps a wrong `allow` from costing much. Three limits do most of the work.

- **Egress.** Block the network by default. Send allowed traffic through a proxy that holds a list of allowed hosts.
  This is the cheapest leg of the trifecta to cut. The agent still reads code and still writes files. It just cannot send them anywhere.
- **Mounts.** Mount the source read-only. Do not mount credential files at all. Give one writable working directory and nothing else.
  The agent cannot leak a file it cannot open.
- **Quotas.** Set limits on CPU, memory, disk, and wall clock time. When a limit is hit, return an error as the tool result.
  Do not kill the process silently. The model can read a timeout and try a shorter command. A silent kill gives it nothing to read.

**Asking without making the user wait twice.** The gate returns `ask`, and the user is now waiting. If the check itself was slow,
they already waited once before the prompt even appeared. A speculative check removes that first wait. The order is:

- The harness starts the permission check in the background.
- The screen shows a progress line right away. That line changes nothing on the system.
- If the check returns `allow` while the line is showing, the tool runs and no prompt appears.
- If the check is still undecided, the line turns into the confirm prompt.

Why this is still safe: the only thing running early is the check. The tool itself still waits for the answer.

---

## Per system

How each agent gates side effects, changes modes, and remembers decisions.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Modes, ordered rules, and sandboxing give fine control. | Auditable in minutes. Rejections feed back to the model. | Denials never loosen; sandboxing fails closed. |
| **Cons** | Many states. Bypass and preapproval paths must stay narrow. | Treats every command the same, remembers nothing. | Policy spans guards, approval, sandbox, presets. |
| **Why** | Asking every time breeds fatigue, so approvals persist. | A prompt plus regexes; the environment limits the damage. | Each concern is its own fail-closed service. |
| **How: gate point** | Before each tool; web, MCP, remote gate separately. | Before a step runs. Enter approves, a comment rejects. | A pre-execute event, then deny-only guards. |
| **How: permission modes** | Default, edit-approved, plan, deny, bypass. | `human`, `confirm`, `yolo`, switched at runtime. | Sandbox mode plus ask or never, bundled as presets. |
| **How: sandbox** | Bash can run inside a sandbox. | The environment class is the sandbox: host, container, wrappers. | Providers wrap each argv; denials come back classified. |
| **How: rule persistence** | Rules merge by priority into session or settings. | Config regexes; matches skip the prompt. | Knob changes are log events; replay folds policy. |

---

## Failure modes

- **Pattern-match bypass.** String deny lists miss shell variants. Parse the command and check what it will actually do. Keep a sandbox behind the parser.
- **Mode left too open.** A broad allow rule or bypass mode can let later risky calls run silently. Scope bypasses and surface the active mode.
- **Approval fatigue.** Asking on every call trains users to approve without reading. Preapprove low-risk classes, but keep destructive actions explicit.
- **Silent denial in a subagent.** A child agent may have no terminal to ask through. Bubble the prompt to the parent instead of failing quietly.
- **Sandbox disabled.** If an allowed command runs outside the sandbox, the permission prompt is the last check. Gate any unsandboxed path behind policy.
- **Exfiltration through approved calls.** Each call can pass the gate on its own, and the session as a whole still reads a secret and sends it out.
  Block the network by default, so the third capability is never there to use.
- **Verified but destructive.** Delete and rebuild passes a result check, because the end state is right. Check the action, not only the end state.
- **Poisoned memory.** An instruction injected into a memory file is read back in every later session. Treat stored memory as untrusted content, never as an operator rule.

---

## Runnable

[`src/`](src/) carries 02 forward and adds:

- [`permissions.py`](src/permissions.py): `decide` over the four modes.
- [`loop.py`](src/loop.py): gates each call in `_dispatch` before running it.

```bash
python sections/03-permission-sandbox/src/test.py         # offline checks, no key
uv run python sections/03-permission-sandbox/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `QueryEngine.ts`, `hooks/useCanUseTool.tsx`, `types/permissions.ts`, `utils/permissions/PermissionUpdate.ts`.
- [Claude Code sandbox and web gates](https://github.com/yasasbanukaofficial/claude-code): `tools/BashTool/shouldUseSandbox.ts`, `tools/WebFetchTool/preapproved.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/interactive.py`, `environments/docker.py`, `environments/extra/bubblewrap.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/subsystems/tools.md`, `docs/subsystems/approval.md`, `docs/subsystems/sandbox.md`, `docs/subsystems/permission-presets.md`,
  `packages/sandbox/sandbox-local/README.md`, `packages/shell/bash-sandbox/README.md`.
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  the memory amplification axis, sandbox egress, mount and quota policy, semantic command parsing, speculative permission checks,
  and constraining the path instead of only the result. Single source for those designs.
- [The lethal trifecta for AI agents](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) (Simon Willison):
  private data access, untrusted content, and external communication as the three capabilities that must not combine.
- [learn-claude-code · s03_permission](https://github.com/shareAI-lab/learn-claude-code): section framing.
