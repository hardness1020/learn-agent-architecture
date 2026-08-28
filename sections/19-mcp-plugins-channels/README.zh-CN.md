# 19 · MCP / plugins / channels

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 通过标准 protocol，让 harness 不改核心程序也能连接外部能力。

harness 能做什么，取决于它有哪些工具。但每个内置工具都必须预先定义 input schema、执行逻辑和错误处理，不可能涵盖所有外部服务。

当用户想连接 issue tracker、部署系统或知识库时，逐一为每个服务和程序语言撰写专属工具，很快就会失去扩展性。

MCP（Model Context Protocol）是一套用来解决这个问题的开放标准。外部服务可以自行声明工具，agent 只需要按照 schema 调用，不必知道工具由谁实现、内部怎么运作。
在 MCP 中，提供工具的服务称为 server，负责连接与调用的 harness 则是 client。

这样一来，不必修改 harness 核心，就能替 agent 加入 Jira 或部署工具。没有 MCP，agent 的能力只能停留在安装时内置的工具集合。

除了 MCP，本章也会介绍建立在它之上的两个机制：plugin 把 server、hook 和 skill 包成可一次安装的软件包；channel 则让 server 主动把消息推回 agent。两者共享同一套 protocol。

---

## 核心机制

![机制图](assets/19-mcp-plugins-channels.png)

连上每个 server，探索它的工具（`tools/list`），把每个工具包装成一个 runtime `Tool`（第 2 章），再把这些合并进 loop 用来 dispatch 的同一个工具池。

名称以 `mcp__<server>__<tool>` 加上命名空间，所以两个 server 永远不会撞名。loop 与 gate 都不变：一个 MCP 工具就是一个 `Tool`，只是它的 `run()` 会通过 transport 对外调用。

- 对每个 server 调用一次 `tools/list`，问它有哪些工具；返回列表里的每一笔规格，都被包成一个 `Tool`。
- 名称加了命名空间并经过标准化，所以它是唯一的，也符合 API 的名称样式。
- 每个工具的 MCP annotation（`readOnlyHint`、`destructiveHint`）成为 gate 读取的权限提示（第 3 章）。
- 合并进那一个 `Registry` 之后，模型会在同一份列表里看到 MCP 工具与内置工具。

### 底层的 wire protocol

2026-07-28 版的 spec 把 protocol 本身改成了 stateless：每个 request 都是独立的，哪台 server 副本都能接。
上面 harness 端做的事（探索、包装、合并）都不变。变的是 client 跟 server 之间实际往来的消息：

- **不用握手了：**以前 client 得先调用 `initialize`、等 server 响应，才能做别的事。
  现在任何 request 都能直接发，每个 request 自己在 `_meta` 里带上 protocol 版本和能力。
  想先确认版本，就调用 `server/discover` 问 server。
- **没有 session 了：**以前 server 靠一个 session header 帮每条连接记状态。
  现在 server 若需要跨调用记东西，就返回一个 handle，client 之后当成普通的工具参数带回来。
- **通知走一条 stream：**以前 client 得挂着一条 GET 连接听变动。
  现在它开一条 `subscriptions/listen` stream，指名要听哪些事件（工具列表变了、resource 变了）。
  list 的结果也多了 `ttlMs` 字段，告诉 client 可以 cache 多久。
- **server 用回复提问，不再回头调用：**以前 server 可以在工具跑到一半时，反过来对 client 发 request
  （问用户一个问题、请模型 sample）。现在它返回一个标着 `input_required` 的中间结果，
  client 把答案附上，重发同一个 request。
- **功能变少了：**Roots、Sampling、Logging 和旧的 HTTP+SSE transport 都列为 deprecated。
  官方 transport 剩两种：本地用 stdio，远端用 Streamable HTTP。

对用 agent 的人来说，画面上什么都没变：旧 server 照常运作，v1 SDK 也继续维护。
好处都出现在用户看不到的地方：远端 server 能挂在 load balancer 后面扩展，第一次调用少一次来回，cache 住的工具列表也省 token。
用到 deprecated 功能的 server 有十二个月的窗口可以迁移。那是 server 作者要做的事，不是用户的事。

### 本章添加：包装探索到的工具

`mcp.py` 把每个探索到的规格变成一个 `Tool`。名称加上命名空间让 server 永不撞名，并标准化到符合 API 的字符集：

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

- `tool_name` 为每个工具加上命名空间；`normalize` 把任何落在 `[a-zA-Z0-9_-]` 之外的字符换成 `_`，以符合 API 名称样式。
- `run` 捕捉了裸工具名与 server 的 `call`，所以 dispatch 被包装的 `Tool` 时会通过 transport 回呼过去。
- `readOnlyHint` annotation 成为 `is_read_only`，这正是权限 gate（第 3 章）用来决定放行或询问的依据。

### 本章添加：探索与合并

`connect` 执行一次探索并返回被包装的工具；调用方把它们合并进 loop 的 `Registry`：

```python
def connect(server, conn):                             # src/mcp.py
    return [wrap(server, spec, conn.call) for spec in conn.list_tools()]
```

- `conn` 是一个活的 transport：正式环境是 `stdio` 或 `http`，demo 里是 in-process。探索并不在意是哪一种。
- 返回的 `Tool` 注册进与内置工具同一个池，所以 `registry.schemas()` 会把它们一起公告，loop 也以相同方式 dispatch。

### 本章添加：channel 与 plugin 配置

本章还剩两个小机制。

第一个是反向的消息流：平常是 agent 去调用 server，但 server 也可以主动把消息推进来，例如一则 Slack 消息到了。harness 把这段文字包上 `<channel>` 标签，接在 agent 下一轮输入的前面，模型就会读到它：

```python
def wrap_channel(source, payload):                     # src/mcp.py
    return f'<{CHANNEL_TAG} source="{source}">{payload}</{CHANNEL_TAG}>'
```

第二个是配置的叠加：同一个 server 可能同时出现在 plugin、用户和项目的配置里，`merge_servers` 依优先序决定谁生效：

```python
def merge_servers(*layers):                            # src/mcp.py
    merged = {}
    for scope in PRECEDENCE:                            # plugin < user < project < local
        for layer in layers:
            merged.update(layer.get(scope, {}))
    return merged
```

- `wrap_channel` 把 Slack、Discord 或 SMS 变成同一套 protocol 上的双向接口；带标签的区块像一则后台备注一样进入队列（第 13 章）。
- `merge_servers` 解决一个在多个 scope 都有定义的 server：`local` 覆盖 `project`，`project` 覆盖 `user`，`user` 覆盖 `plugin`。

channel 的消息谁都能发：从 Slack 或 SMS 进来的文字不一定出自用户本人，可能是垃圾消息，甚至是想操纵 agent 的指令。所以消息得先通过 gate 检查，才能变成一个 turn（Hermes 对每则进来的消息，在 auth 之前就 fire `pre_gateway_dispatch`）：

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

- 一个 gate 可以 drop（垃圾消息、不明寄件者）或 rewrite（遮蔽机密），发生在 loop 看到文字之前。
- 返回 `None` 代表这则消息不会变成任何 turn，垃圾输入连一次模型调用都不用花。

### 如何集成到现有架构

demo 会探索一个 server，再执行一轮 agent。模型可以直接调用这个 MCP 工具，不需要知道内部实现：

```python
reg = Registry()
for t in mcp.connect("kb", KBServer()):                # discover, wrap, merge
    reg.register(t)
run_turn([...goal...], model, reg, Session(mode=DEFAULT))   # the one agent call
```

- 模型在它的工具列表里看到 `mcp__kb__search` 就在任何内置工具旁边，并调用它；它永远不会得知是谁写了这个工具。
- 这个工具是只读的，所以 gate 不提示就放行。一个具破坏性的工具则会询问，或由一条以完整名称为键的规则预先批准。
- loop 不变。MCP 只是往池里加工具；下游的一切都是第 2 章的 dispatch 与第 3 章的 gating。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book 和 MCP spec，也未经下面表格的系统证实。

**三种 primitive，只有一种进池子：**一个 server 可以提供三种东西，但只有 tool 会进到上面那个池子。

- **Tools** 是动作。模型自己挑一个来调用。`tools/list` 返回的就是这些，上面的代码包的也是它们。
- **Resources** 是可以读的数据，每一笔都有一个 URI：一个文件、一张表、一页 wiki。client 把它抓下来，把内容放进 context。模型不会去调用它。
- **Prompts** 是 server 给的模板。它通常是用户可以下的一个指令，不是模型自己挑的东西。

**resource 不会出现在工具列表上：**Claude Code 不会把它们一个一个公告出去，它只放两个工具，一个列出 resource，一个把 resource 读出来。
所以一个放了上千份文件的 server，在工具列表里还是只占两格。

**连上去和公告出去，是两个决定：**连上一个 server，换到的是互通；把它的工具公告给模型，花掉的是 context。
前面那件事可以做，后面那件事不一定要做满。

**公告出去要付什么代价：**每一个公告出去的工具，每次 request 都在花 token。名称、描述、完整的 input schema，全都排在任务前面。
五个 server 加起来，这段文字可能比任务本身还长。列表一长，模型也更容易挑错工具（第 2 章）。

**公告的程度有三种可以挑：**一个 server 一个 server 决定公告多少，不是全部一起套。

- **全部都公告：**最单纯。适合那种几乎每一轮都会用到的 server。
- **只公告一份索引：**先给名称和一句话说明。等模型指名要哪个工具，再把完整的 schema 载进来（探索那一侧在第 2 章）。
- **只开一扇门：**只公告一个工具，参数是 server 名称和工具名称，其他都放在它后面。agent 只要付一份 schema，不用付五十份。

**protocol 完全没管这件事：**它只规定工具怎么列、怎么调用。有多少工具会进到 prompt，是 client 自己决定的。
所以延后加载是你要去自己的 harness 里确认的配置，server 不能假设它一定开着。

---

## 不同系统怎么做

harness 如何伸手触及自身之外。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | 任何服务、任何语言都接得上，不用改 harness。 | 其他 client 能把它当 MCP server 来用。 | server 就是一份配置，不重启也能换掉一台。 |
| **限制** | 每个 server 都是新的攻击面，annotation 还是自己报的。 | channel 谁都能发：垃圾消息，想操纵 agent 的话也一样。 | 只接工具，也没有聊天 channel 能把消息推进来。 |
| **设计原因** | 少了 MCP，能力就停在安装当下内置的那一套。 | agent 同时是 MCP client 和 MCP server。 | 每样东西都是 plugin，MCP server 也只是其中一个。 |
| **做法：transports** | 六种，从本地 stdio 到远端 http，各连各的池。 | MCP 双向，加上聊天平台 adapter。 | 本地 stdio 和 streaming http，一台 server 一个 plugin。 |
| **做法：plugin format** | 一个 plugin 打包 server、hook、skill，按优先序合并。 | 一份 manifest 加一个注册入口。 | 一列一列的配置。patch 用 id 整列换掉。 |
| **做法：tool pool assembly** | 复制、加命名空间，annotation 成为 gate 的权限提示。 | plugin 与 MCP 工具进同一个 registry。 | 一台 server 的工具整批换上，出错就整批回滚。 |

---

## 常见问题

- **撞名（Name collisions）：**两个 server 都公开 `search`。`mcp__server__tool` 命名空间避免了冲突；但一个名称含 `__` 的 server 仍会被解析错误，所以名称要保持简单。
- **工具列表膨胀（Tool-list bloat）：**太多 server 会造成庞大的工具列表，既花 token 又干扰选择（第 2 章）。
  缓解：截断描述，并且一个 server 一个 server 决定公告多少，不要每次 request 都把所有 schema 送一遍。
- **connect 之后池过时：**一个在 session 中途加入的 server 不在 cache 的工具列表里，于是模型永远看不到它。缓解：变动时重建池并重建 prompt（第 8 章）；
  2026-07-28 版的 spec 为此加了走 `subscriptions/listen` 的 `toolsListChanged` 通知和 `ttlMs` 提示。
- **连接抖动（Connection churn）：**一个不稳的 server 会超时、重置，或 token 过期。缓解：反覆失败后重连、`401` 时重新验证、为每次调用设超时（第 11 章）。
  stateless 版拿掉了 stream 续传，所以中断的 request 要当成一个新 request 重发，不是接着传。
- **被过度信任的副作用：**一个 server 把具破坏性的工具标成 `readOnlyHint: true` 以跳过提示。缓解：以完整名称设一条规则照样 gate 它（第 3 章）。
- **描述投毒（Description poisoning）：**工具描述是 server 自己写的文字，模型却把它当成指令在读。
  server 可以在里面塞一句话，例如叫模型先读用户的密钥文件、再一起传过来。模型真的有可能照做。
  缓解：装一个 server 之前，先把描述读过一遍。描述改了，就当成代码改了那样审。
- **工具遮蔽（Tool shadowing）：**所有 server 共享同一份 prompt。所以一个 server 的描述可以讲到另一个 server 的工具，
  说付款工具坏了，再把调用拉到自己身上。
  缓解：命名空间挡得住撞名，挡不住这个。没审过的 server，别放进握有真实凭证的 session。
- **被劫持的更新（Hijacked updates）：**一个 server 审过了，下次启动时却换上新的代码和新的描述。protocol 不会再问用户一次。
  缓解：把版本钉住。升级之后把描述再读一遍。每个 server 各给一份最小权限的凭证，这样一个 server 坏掉，也伸不到别的 server 的范围。

---

## 动手跑跑看

[`src/`](src/) 承接第 18 章并加上：

- [`mcp.py`](src/mcp.py)：探索与包装、plugin 配置合并、channel 包装，以及入站 gate（`gate_inbound`）。
- [`test.py`](src/test.py)：探索与命名空间、权限提示的对应、连同 gate 合并进池、配置优先序、channel 标签，以及入站的 drop 与 rewrite。
- [`demo.py`](src/demo.py)：一轮 agent 通过探索到的 `mcp__kb__search`，直接调用一个 in-process MCP 工具。

loop 与 dispatch 都不变。MCP 只是往第 2 章的池里加工具；第 3 章的 gate 读取它们自我声明的 annotation。

```bash
python sections/19-mcp-plugins-channels/src/test.py         # offline checks, no key
uv run python sections/19-mcp-plugins-channels/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code MCP transport](https://github.com/yasasbanukaofficial/claude-code)：
  `services/mcp/types.ts`（`TransportSchema`）、`client.ts`（`MCPTool` cloning、`buildMcpToolName`）、`normalization.ts`（`normalizeNameForMCP`）。
- [Claude Code MCP config and channels](https://github.com/yasasbanukaofficial/claude-code)：
  `config.ts`（precedence）、`channelNotification.ts`（`CHANNEL_TAG`），加上 `McpAuthTool`、`ListMcpResourcesTool`、`ReadMcpResourceTool`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/architecture.md`、`docs/cordis-primer.md`、`packages/acp/acp/README.md`、`packages/extensions/tool-cordis/README.md`。
- [Claude Code plugins](https://github.com/yasasbanukaofficial/claude-code)：`plugins/builtinPlugins.ts`、`plugins/bundled/`、`types/plugin.ts`，加上 `remote/` 与 `bridge/`。
- [Hermes Agent 源代码](https://github.com/NousResearch/hermes-agent)：
  `mcp_serve.py`、`hermes_cli/plugins.py`（`PluginManager`、`VALID_HOOKS`）、`gateway/platforms/`、`gateway/platform_registry.py`、`plugins/platforms/`。
- [MCP specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) 与它的
  [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)：stateless protocol、tools / resources / prompts 三种 primitive、
  `server/discover`、`subscriptions/listen`、MRTR、deprecation 列表。
- MCP blog：[the future of transports](https://blog.modelcontextprotocol.io/posts/2025-12-19-mcp-transport-future/)（protocol 为什么走向 stateless）、
  [SDK betas for 2026-07-28](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/)（v2 SDK 与向后相容）。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter4.md`，以中文原版为准。工具生态那一节：
  MCP 的 primitive、公告 schema 的 context 开销，以及信任模型（描述投毒、工具遮蔽、被劫持的更新、凭证范围）。
- 章节定位：[learn-claude-code · s19_mcp_plugin](https://github.com/shareAI-lab/learn-claude-code)。
