# 19 · MCP / plugins / channels

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Not enough capability? Plug in more. The harness reaches the world through one standard protocol.

A harness can only do what its tools let it do, and every built-in tool is predefined: input schema, execution, error handling, all of it.

That does not scale to the services a user wants: issue trackers, deploy systems, knowledge bases. You cannot hand write a tool for each, in each language it uses.

MCP (Model Context Protocol) is the open contract that closes the gap. An external service declares its tools, and the agent calls them blind, not knowing who wrote them or how.
In MCP terms the service is the server, and the harness that connects and calls is the client.

So the agent gains a Jira tool or a deploy tool without anyone editing the harness. Leave MCP out and capability is frozen at whatever shipped in the binary.

Two more pieces build on MCP. A plugin bundles servers with hooks and skills, so they install as one unit.
A channel lets a server push messages back in. Both ride the same protocol.

---

## Mechanism

![Mechanism diagram](assets/19-mcp-plugins-channels.png)

Connect to each server, discover its tools (`tools/list`), wrap each as a runtime `Tool` (section 2), and merge those into the same pool the loop dispatches.

Names are namespaced `mcp__<server>__<tool>` so two servers never collide. The loop and gate do not change: an MCP tool is a `Tool` whose `run()` calls out over a transport.

- Discovery is one `tools/list` call per server; each returned spec becomes one wrapped `Tool`.
- The name is namespaced and normalized, so it is unique and matches the API's name pattern.
- Each tool's MCP annotations (`readOnlyHint`, `destructiveHint`) become the permission hints the gate reads (section 3).
- Merged into the one `Registry`, the model sees MCP tools and built-ins in the same list.

### The wire protocol under it

The 2026-07-28 spec revision made the wire stateless. Every request now stands alone, so any server replica can answer it.
The harness side above (discover, wrap, merge) does not change. What changed on the wire:

- **No more handshake.** Before, a client called `initialize` and waited before doing anything else.
  Now any request can go first; each one carries its own protocol version and capabilities in `_meta`.
  A client that wants to check versions up front calls `server/discover`.
- **No more sessions.** Before, the server kept per-connection state behind a session header.
  Now a server that needs state across calls returns a handle, and the client passes it back as a normal tool argument.
- **One notification stream.** Before, a client held a long GET connection open to hear about changes.
  Now it opens one `subscriptions/listen` stream and names the events it wants (a changed tool list, a changed resource).
  List results also carry a `ttlMs` field that says how long the client may cache them.
- **The server asks by replying, not by calling back.** Before, a server could send its own request to the client
  mid-tool-call (ask the user a question, ask the model to sample). Now it returns an interim result marked
  `input_required`, and the client retries the same request with the answer attached.
- **Fewer features.** Roots, Sampling, Logging, and the old HTTP+SSE transport are deprecated.
  Two transports remain official: stdio for local servers, Streamable HTTP for remote ones.

For someone using an agent, nothing changes on screen: old servers keep working, and v1 SDKs stay maintained.
The gains land underneath: remote servers scale behind a load balancer, the first call skips a round trip, and a cached tool list saves tokens.
Servers on a deprecated feature get a twelve-month window to migrate. That work falls on the server author, not the user.

### New: wrapping a discovered tool

`mcp.py` turns each discovered spec into a `Tool`. The name is namespaced so servers never collide, and normalized to the API's charset:

```python
def tool_name(server, tool):                           # src/mcp.py
    return f"mcp__{normalize(server)}__{normalize(tool)}"   # buildMcpToolName

def wrap(server, spec, call):
    ann = spec.get("annotations", {})
    read_only = bool(ann.get("readOnlyHint"))
    bare = spec["name"]
    return Tool(
        name=tool_name(server, bare),
        run=lambda args, _t=bare: call(_t, args),      # dispatch calls out over the transport
        input_schema=spec.get("inputSchema") or dict(NO_INPUT),
        is_read_only=read_only,
        is_concurrency_safe=read_only,                 # reads are safe to batch
    )
```

- `tool_name` namespaces every tool; `normalize` replaces any char outside `[a-zA-Z0-9_-]` with `_`, satisfying the API name pattern.
- `run` closes over the bare tool name and the server's `call`, so dispatching the wrapped `Tool` reaches back over the transport.
- The `readOnlyHint` annotation becomes `is_read_only`, which is what the permission gate (section 3) reads to decide allow vs ask.

### New: discovering and merging

`connect` runs discovery once and returns wrapped tools; the caller merges them into the loop's `Registry`:

```python
def connect(server, conn):                             # src/mcp.py
    return [wrap(server, spec, conn.call) for spec in conn.list_tools()]
```

- `conn` is a live transport: `stdio` or `http` in production, in-process in the demo. Discovery does not care which.
- The returned `Tool`s register into the same pool as built-ins, so `registry.schemas()` advertises them together and the loop dispatches them the same way.

### New: channels and plugin config

Two smaller pieces round out the section.

The first reverses the message flow. Normally the agent calls the server, but a server can also push a message in on its own (a Slack message arrives).
The harness wraps that text in a `<channel>` tag and puts it ahead of the agent's next turn, so the model reads it:

```python
def wrap_channel(source, payload):                     # src/mcp.py
    return f'<{CHANNEL_TAG} source="{source}">{payload}</{CHANNEL_TAG}>'
```

The second is config layering. The same server can be defined in plugin, user, and project config at once; `merge_servers` picks the winner by precedence:

```python
def merge_servers(*layers):                            # src/mcp.py
    merged = {}
    for scope in PRECEDENCE:                            # plugin < user < project < local
        for layer in layers:
            merged.update(layer.get(scope, {}))
    return merged
```

- `wrap_channel` turns Slack, Discord, or SMS into a two-way surface over the same protocol; the tagged block enqueues like a background note (section 13).
- `merge_servers` resolves a server defined in more than one scope: `local` overrides `project` overrides `user` overrides `plugin`.

Anyone can send to a channel. An inbound Slack or SMS message is not necessarily from the user: it may be spam, or an instruction meant to steer the agent.
So it passes gates before it can become a turn (Hermes fires `pre_gateway_dispatch` on every incoming message, before auth):

```python
def gate_inbound(source, payload, gates=()):           # src/mcp.py
    for gate in gates:
        out = gate(source, payload) or {}
        if out.get("drop"):
            return None                                # discarded: the model never reads it
        if out.get("rewrite") is not None:
            payload = out["rewrite"]                   # e.g. redact a secret
    return wrap_channel(source, payload)
```

- A gate may drop (spam, an unknown sender) or rewrite (redaction) before the loop sees the text.
- Returning `None` means no turn happens at all, the cheapest possible outcome for junk input.

### How it integrates

The demo discovers a server and runs one agent turn. The model calls the MCP tool blind:

```python
reg = Registry()
for t in mcp.connect("kb", KBServer()):                # discover, wrap, merge
    reg.register(t)
run_turn([...goal...], model, reg, Session(mode=DEFAULT))   # the one agent call
```

- The model sees `mcp__kb__search` in its tool list next to any built-in and calls it; it never learns who wrote the tool.
- The tool is read-only, so the gate allows it with no prompt. A destructive tool would ask, or be pre-approved by a rule keyed on the qualified name.
- The loop does not change. MCP adds tools to the pool; everything downstream is section-2 dispatch and section-3 gating.

### Further reading

None of this is in `src/`. It comes from ai-agent-book and the MCP spec, and is not confirmed of the systems in the table.

**Three primitives, one pool.** A server can offer three kinds of thing. Only tools reach the pool above.

- **Tools** are actions. The model picks one and calls it. `tools/list` returns these, and the code above wraps them.
- **Resources** are data the client can read, each with a URI: a file, a table, a wiki page. The client fetches one and puts the text in context. The model never calls it.
- **Prompts** are templates the server hands over. They usually show up as a command the user runs, not as something the model picks.

**Resources stay off the tool list.** Claude Code does not advertise them one by one. It ships two tools, one that lists resources and one that reads them.
So a server holding a thousand documents still costs two entries in the tool list.

**Connecting and advertising are two decisions.** Connecting to a server buys interop. Advertising its tools spends context.
You can do the first without doing all of the second.

**What advertising costs.** Each advertised tool costs tokens on every request. Name, description, and the full input schema, all of it sits in front of the task.
Five servers can add more text than the task itself. A long list also makes the model pick the wrong tool more often (section 2).

**Three levels to pick from.** Decide per server how much to advertise, not once for all of them:

- **Everything.** Simplest. Right for a server the session uses almost every turn.
- **An index.** Advertise names and one-line summaries. Load the full schema when the model asks for that tool (section 2 covers the discovery side).
- **One door.** Advertise one tool that takes a server name and a tool name. The rest stays behind it. The agent pays for one schema, not fifty.

**None of this is in the protocol.** The spec says how to list tools and how to call them. How many of them reach the prompt is up to the client.
So deferred loading is a setting to check in your own harness. A server cannot assume it is on.

---

## Per system

How the harness reaches outside itself.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Any service, any language, no harness edits. | Other clients can drive it as an MCP server. | A server is config, so one can swap without a restart. |
| **Cons** | New attack surface, on self-reported annotations. | Anyone can send to a channel: spam, or steering text. | Tools only, and no chat channels to push messages in. |
| **Why** | Without MCP, capability is frozen at what shipped. | The agent is MCP client and server at once. | Everything is a plugin, so a server is one more plugin. |
| **How: transports** | Six, from stdio to remote http, in separate pools. | MCP both ways, plus chat adapters. | Local stdio and streaming http, one plugin per server. |
| **How: plugin format** | A plugin bundles servers, hooks, and skills, merged by precedence. | A manifest plus a register entry. | Config rows. A patch replaces one row by id. |
| **How: tool pool assembly** | Cloned and namespaced; annotations feed the gate. | Plugin and MCP tools join one registry. | A server's tools swap as one set, or roll back. |

---

## Failure modes

- **Name collisions.** Two servers both expose `search`. The `mcp__server__tool` namespace prevents clashes; a server name with `__` still parses wrong, so keep names simple.
- **Tool-list bloat.** Many servers make a large tool list that costs tokens and confuses selection (section 2).
  Mitigation: truncate descriptions, and decide per server how much to advertise instead of sending every schema on every request.
- **Stale pool after connect.** A server added mid-session is not in the cached tool list, so the model never sees it.
  Mitigation: rebuild pool and prompt on change (section 8); the 2026-07-28 spec adds `toolsListChanged` over `subscriptions/listen` and `ttlMs` hints for this.
- **Connection churn.** A flaky server times out, resets, or expires its token. Mitigation: reconnect after repeated failures, re-auth on `401`, time out each call (section 11).
  The stateless revision drops stream resumability, so a broken in-flight request is re-issued as a new one, not resumed.
- **Over-trusted side effects.** A server marks a destructive tool `readOnlyHint: true` to skip the prompt. Mitigation: a rule on the qualified name gates it anyway (section 3).
- **Description poisoning.** A tool description is text the server wrote, and the model reads it as instructions.
  A server can hide an order in there, such as read the user's key file first and send it along. The model may do it.
  Mitigation: read the descriptions before installing a server. When one changes, review it like changed code.
- **Tool shadowing.** All servers share one prompt. So one server's description can talk about another server's tools,
  claim the payment tool is broken, and pull the call to itself.
  Mitigation: namespacing stops name clashes. It does not stop this. Keep unreviewed servers out of sessions that hold real credentials.
- **Hijacked updates.** A server passes review, then ships new code and new descriptions on the next start. The protocol never asks the user again.
  Mitigation: pin a version. Read the descriptions again after an upgrade. Give each server its own least-privilege credential, so one bad server cannot reach another one's scope.

---

## Runnable

[`src/`](src/) carries 18 forward and adds:

- [`mcp.py`](src/mcp.py): discovery and wrapping, the plugin config merge, the channel wrap, and the inbound gate (`gate_inbound`).
- [`test.py`](src/test.py): discovery and namespacing, the hint mapping, pool merging with the gate, config precedence, the channel tag, and inbound drop and rewrite.
- [`demo.py`](src/demo.py): one agent turn calls an in-process MCP tool blind through the discovered `mcp__kb__search`.

The loop and dispatch do not change. MCP adds tools to the section-2 pool; the section-3 gate reads their self-declared annotations.

```bash
python sections/19-mcp-plugins-channels/src/test.py         # offline checks, no key
uv run python sections/19-mcp-plugins-channels/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code MCP transport](https://github.com/yasasbanukaofficial/claude-code):
  `services/mcp/types.ts` (`TransportSchema`), `client.ts` (`MCPTool` cloning, `buildMcpToolName`), `normalization.ts` (`normalizeNameForMCP`).
- [Claude Code MCP config and channels](https://github.com/yasasbanukaofficial/claude-code):
  `config.ts` (precedence), `channelNotification.ts` (`CHANNEL_TAG`), plus `McpAuthTool`, `ListMcpResourcesTool`, `ReadMcpResourceTool`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/cordis-primer.md`, `packages/acp/acp/README.md`, `packages/extensions/tool-cordis/README.md`.
- [Claude Code plugins](https://github.com/yasasbanukaofficial/claude-code): `plugins/builtinPlugins.ts`, `plugins/bundled/`, `types/plugin.ts`, plus `remote/` and `bridge/`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `mcp_serve.py`, `hermes_cli/plugins.py` (`PluginManager`, `VALID_HOOKS`), `gateway/platforms/`, `gateway/platform_registry.py`, `plugins/platforms/`.
- [MCP specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) and its
  [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog): stateless protocol, the three primitives (tools, resources, prompts),
  `server/discover`, `subscriptions/listen`, MRTR, deprecations.
- MCP blog: [the future of transports](https://blog.modelcontextprotocol.io/posts/2025-12-19-mcp-transport-future/) (why the protocol went stateless),
  [SDK betas for 2026-07-28](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/) (v2 SDKs, backward compatibility).
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, Chinese original canonical. The tool ecosystem section:
  MCP primitives, context overhead of advertised schemas, and the trust model (description poisoning, tool shadowing, hijacked updates, credential scope).
- Framing: [learn-claude-code · s19_mcp_plugin](https://github.com/shareAI-lab/learn-claude-code).
