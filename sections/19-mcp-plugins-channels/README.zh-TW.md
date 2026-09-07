# 19 · MCP / plugins / channels

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> 能力不夠？再插上更多。harness 透過一套標準 protocol 接到外面的世界。

harness 能做什麼，取決於它有哪些工具。但每個內建工具都必須預先定義 input schema、執行邏輯和錯誤處理，不可能涵蓋所有外部服務。

當使用者想連接 issue tracker、部署系統或知識庫時，逐一為每個服務和程式語言撰寫專屬工具，很快就會失去擴展性。

MCP（Model Context Protocol）是一套用來解決這個問題的開放標準。外部服務可以自行宣告工具，agent 只需要按照 schema 呼叫，不必知道工具由誰實作、內部怎麼運作。
在 MCP 中，提供工具的服務稱為 server，負責連線與呼叫的 harness 則是 client。

這樣一來，不必修改 harness 核心，就能替 agent 加入 Jira 或部署工具。沒有 MCP，agent 的能力只能停留在安裝時內建的工具集合。

有兩個機制建立在它之上。plugin 把 server、hook 和 skill 包成可一次安裝的套件；channel 則讓 server 主動把訊息推回 agent。兩者共用同一套 protocol。

---

## 核心機制

![機制圖](assets/19-mcp-plugins-channels.png)

連上每個 server，探索它的工具（`tools/list`），把每個工具包裝成一個 runtime `Tool`（第 2 章），再把這些合併進 loop 用來 dispatch 的同一個工具池。

名稱以 `mcp__<server>__<tool>` 加上命名空間，所以兩個 server 永遠不會撞名。loop 與 gate 都不變：一個 MCP 工具就是一個 `Tool`，只是它的 `run()` 會透過 transport 對外呼叫。

- 對每個 server 呼叫一次 `tools/list`，問它有哪些工具；回傳清單裡的每一筆規格，都被包成一個 `Tool`。
- 名稱加了命名空間並經過正規化，所以它是唯一的，也符合 API 的名稱樣式。
- 每個工具的 MCP annotation（`readOnlyHint`、`destructiveHint`）成為 gate 讀取的權限提示（第 3 章）。
- 合併進那一個 `Registry` 之後，模型會在同一份清單裡看到 MCP 工具與內建工具。

### 底層的 wire protocol

2026-07-28 版的 spec 把 protocol 本身改成了 stateless：每個 request 都是獨立的，哪台 server 副本都能接。
上面 harness 端做的事（探索、包裝、合併）都不變。變的是 client 跟 server 之間實際往來的訊息：

- **不用握手了：**以前 client 得先呼叫 `initialize`、等 server 回應，才能做別的事。
  現在任何 request 都能直接發，每個 request 自己在 `_meta` 裡帶上 protocol 版本和能力。
  想先確認版本，就呼叫 `server/discover` 問 server。
- **沒有 session 了：**以前 server 靠一個 session header 幫每條連線記狀態。
  現在 server 若需要跨呼叫記東西，就回傳一個 handle，client 之後當成普通的工具參數帶回來。
- **通知走一條 stream：**以前 client 得掛著一條 GET 連線聽變動。
  現在它開一條 `subscriptions/listen` stream，指名要聽哪些事件（工具清單變了、resource 變了）。
  list 的結果也多了 `ttlMs` 欄位，告訴 client 可以 cache 多久。
- **server 用回覆提問，不再回頭呼叫：**以前 server 可以在工具跑到一半時，反過來對 client 發 request
  （問使用者一個問題、請模型 sample）。現在它回傳一個標著 `input_required` 的中間結果，
  client 把答案附上，重發同一個 request。
- **功能變少了：**Roots、Sampling、Logging 和舊的 HTTP+SSE transport 都列為 deprecated。
  官方 transport 剩兩種：本地用 stdio，遠端用 Streamable HTTP。

對用 agent 的人來說，畫面上什麼都沒變：舊 server 照常運作，v1 SDK 也繼續維護。
好處都出現在使用者看不到的地方：遠端 server 能掛在 load balancer 後面擴展，第一次呼叫少一趟來回，cache 住的工具清單也省 token。
用到 deprecated 功能的 server 有十二個月的窗口可以遷移。那是 server 作者要做的事，不是使用者的事。

### 本章新增：包裝探索到的工具

`mcp.py` 把每個探索到的規格變成一個 `Tool`。名稱加上命名空間讓 server 永不撞名，並正規化到符合 API 的字元集：

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

- `tool_name` 為每個工具加上命名空間；`normalize` 把任何落在 `[a-zA-Z0-9_-]` 之外的字元換成 `_`，以符合 API 名稱樣式。
- `run` 把裸工具名和 server 的 `call` 記在身上，所以 dispatch 被包裝的 `Tool` 時會透過 transport 回呼過去。
- `readOnlyHint` annotation 成為 `is_read_only`，這正是權限 gate（第 3 章）用來決定放行或詢問的依據。

### 本章新增：探索與合併

`connect` 執行一次探索並回傳被包裝的工具；呼叫端把它們合併進 loop 的 `Registry`：

```python
def connect(server, conn):                             # src/mcp.py
    return [wrap(server, spec, conn.call) for spec in conn.list_tools()]
```

- `conn` 是一個活的 transport：正式環境是 `stdio` 或 `http`，demo 裡是 in-process。探索並不在意是哪一種。
- 回傳的 `Tool` 註冊進與內建工具同一個池，所以 `registry.schemas()` 會把它們一起公告，loop 也以相同方式 dispatch。

### 本章新增：channel 與 plugin 設定

本章還剩兩個小機制。

第一個是反向的訊息流：平常是 agent 去呼叫 server，但 server 也可以主動把訊息推進來，例如一則 Slack 訊息到了。harness 把這段文字包上 `<channel>` 標籤，接在 agent 下一輪輸入的前面，模型就會讀到它：

```python
def wrap_channel(source, payload):                     # src/mcp.py
    return f'<{CHANNEL_TAG} source="{source}">{payload}</{CHANNEL_TAG}>'
```

第二個是設定的疊加：同一個 server 可能同時出現在 plugin、使用者和專案的設定裡，`merge_servers` 依優先序決定誰生效：

```python
def merge_servers(*layers):                            # src/mcp.py
    merged = {}
    for scope in PRECEDENCE:                            # plugin < user < project < local
        for layer in layers:
            merged.update(layer.get(scope, {}))
    return merged
```

- `wrap_channel` 把 Slack、Discord 或 SMS 變成同一套 protocol 上的雙向介面；帶標籤的區塊像一則背景備註一樣進入佇列（第 13 章）。
- `merge_servers` 解決一個在多個 scope 都有定義的 server：`local` 覆蓋 `project`，`project` 覆蓋 `user`，`user` 覆蓋 `plugin`。

channel 的訊息誰都能發：從 Slack 或 SMS 進來的文字不一定出自使用者本人，可能是垃圾訊息，甚至是想操縱 agent 的指令。所以訊息得先通過 gate 檢查，才能變成一個 turn（Hermes 對每則進來的訊息，在 auth 之前就 fire `pre_gateway_dispatch`）：

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

- 一個 gate 可以 drop（垃圾訊息、不明寄件者）或 rewrite（遮蔽機密），發生在 loop 看到文字之前。
- 回傳 `None` 代表這則訊息不會變成任何 turn，垃圾輸入連一次模型呼叫都不用花。

### 如何接進現有架構

demo 會探索一個 server，再執行一輪 agent。模型可以直接呼叫這個 MCP 工具，不需要知道內部實作：

```python
reg = Registry()
for t in mcp.connect("kb", KBServer()):                # discover, wrap, merge
    reg.register(t)
run_turn([...goal...], model, reg, Session(mode=DEFAULT))   # the one agent call
```

- `mcp__kb__search` 就出現在內建工具旁邊，模型在工具清單裡看到就直接呼叫。它永遠不會得知是誰寫了這個工具。
- 這個工具是唯讀的，所以 gate 不提示就放行。一個具破壞性的工具則會詢問，或由一條以完整名稱為鍵的規則預先核准。
- loop 不變。MCP 只是往池裡加工具；下游的一切都是第 2 章的 dispatch 與第 3 章的 gating。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book 和 MCP spec，也未經下面表格的系統證實。

**三種 primitive，只有一種進池子：**一個 server 可以提供三種東西，但只有 tool 會進到上面那個池子。

- **Tools** 是動作。模型自己挑一個來呼叫。`tools/list` 回傳的就是這些，上面的程式碼包的也是它們。
- **Resources** 是可以讀的資料，每一筆都有一個 URI：一個檔案、一張表、一頁 wiki。client 把它抓下來，把內容放進 context。模型不會去呼叫它。
- **Prompts** 是 server 給的範本。它通常是使用者可以執行的一個指令，不是模型自己挑的東西。

**resource 不會出現在工具清單上：**Claude Code 不會把它們一個一個公告出去，它只放兩個工具，一個列出 resource，一個把 resource 讀出來。
所以一個放了上千份文件的 server，在工具清單裡還是只佔兩格。

**連上去和公告出去，是兩個決定：**連上一個 server，換到的是互通；把它的工具公告給模型，花掉的是 context。
前面那件事可以做，後面那件事不一定要做滿。

**公告出去要付什麼代價：**每一個公告出去的工具，每次 request 都在花 token。名稱、描述、完整的 input schema，全都排在任務前面。
五個 server 加起來，這段文字可能比任務本身還長。清單一長，模型也更容易挑錯工具（第 2 章）。

**公告的程度有三種可以挑：**一個 server 一個 server 決定公告多少，不是全部一起套。

- **全部都公告：**最單純。適合那種幾乎每一輪都會用到的 server。
- **只公告一份索引：**先給名稱和一句話說明。等模型指名要哪個工具，再把完整的 schema 載進來（探索那一側在第 2 章）。
- **只開一扇門：**只公告一個工具，參數是 server 名稱和工具名稱，其他都放在它後面。agent 只要付一份 schema，不用付五十份。

**protocol 完全沒管這件事：**它只規定工具怎麼列、怎麼呼叫。有多少工具會進到 prompt，是 client 自己決定的。
所以延後載入是你要去自己的 harness 裡確認的設定，server 不能假設它一定開著。

---

## 不同系統怎麼做

harness 如何伸手觸及自身之外。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | 任何服務、任何語言都接得上，不用改 harness。 | 其他 client 能把它當 MCP server 來用。 | server 就是一份設定，不重啟也能換掉一台。 |
| **限制** | 每個 server 都是新的攻擊面，annotation 還是自己報的。 | channel 誰都能發：可能是垃圾訊息，也可能是想操縱 agent 的文字。 | 只接工具，也沒有聊天 channel 能把訊息推進來。 |
| **設計原因** | 少了 MCP，能力就停在安裝當下內建的那一套。 | agent 同時是 MCP client 和 MCP server。 | 每樣東西都是 plugin，server 也只是其中一個。 |
| **做法：transports** | 六種，從本地 stdio 到遠端 http，各連各的池。 | MCP 雙向，加上聊天平台 adapter。 | 本地 stdio 和 streaming http，一台 server 一個 plugin。 |
| **做法：plugin format** | 一個 plugin 打包 server、hook、skill，按優先序合併。 | 一份 manifest 加一個註冊進入點。 | 一列一列的設定。patch 用 id 整列換掉。 |
| **做法：tool pool assembly** | 複製、加命名空間，annotation 成為 gate 的權限提示。 | plugin 與 MCP 工具進同一個 registry。 | 一台 server 的工具整批換上，或是整批回滾。 |

---

## 常見問題

- **撞名（Name collisions）：**兩個 server 都公開 `search`。`mcp__server__tool` 命名空間避免了衝突；但一個名稱含 `__` 的 server 仍會被解析錯誤，所以名稱要保持簡單。
- **工具清單膨脹（Tool-list bloat）：**太多 server 會造成龐大的工具清單，既花 token 又干擾選擇（第 2 章）。
  緩解：截斷描述，並且一個 server 一個 server 決定公告多少，不要每次 request 都把所有 schema 送一遍。
- **connect 之後池過時：**一個在 session 中途加入的 server 不在 cache 的工具清單裡，於是模型永遠看不到它。緩解：變動時重建池並重建 prompt（第 8 章）；
  2026-07-28 版的 spec 為此加了走 `subscriptions/listen` 的 `toolsListChanged` 通知和 `ttlMs` 提示。
- **連線抖動（Connection churn）：**一個不穩的 server 會逾時、重置，或 token 過期。緩解：反覆失敗後重連、`401` 時重新驗證、為每次呼叫設逾時（第 11 章）。
  stateless 版拿掉了 stream 續傳，所以中斷的 request 要當成一個新 request 重發，不是接著傳。
- **被過度信任的副作用：**一個 server 把具破壞性的工具標成 `readOnlyHint: true` 以跳過提示。緩解：以完整名稱設一條規則照樣 gate 它（第 3 章）。
- **描述投毒（Description poisoning）：**工具描述是 server 自己寫的文字，模型卻把它當成指令在讀。
  server 可以在裡面塞一句話，例如叫模型先讀使用者的金鑰檔、再一起傳過來。模型真的有可能照做。
  緩解：裝一個 server 之前，先把描述讀過一遍。描述改了，就當成程式碼改了那樣審。
- **工具遮蔽（Tool shadowing）：**所有 server 共用同一份 prompt。所以一個 server 的描述可以講到另一個 server 的工具，
  說付款工具壞了，再把呼叫拉到自己身上。
  緩解：命名空間擋得住撞名，擋不住這個。沒審過的 server，別放進握有真實憑證的 session。
- **被劫持的更新（Hijacked updates）：**一個 server 審過了，下次啟動時卻換上新的程式碼和新的描述。protocol 不會再問使用者一次。
  緩解：把版本釘住。升級之後把描述再讀一遍。每個 server 各給一份最小權限的憑證，這樣一個 server 壞掉，也伸不到別的 server 的範圍。

---

## 動手跑跑看

[`src/`](src/) 承接第 18 章並加上：

- [`mcp.py`](src/mcp.py)：探索與包裝、plugin 設定合併、channel 包裝，以及入站 gate（`gate_inbound`）。
- [`test.py`](src/test.py)：探索與命名空間、權限提示的對應、連同 gate 合併進池、設定優先序、channel 標籤，以及入站的 drop 與 rewrite。
- [`demo.py`](src/demo.py)：一輪 agent 透過探索到的 `mcp__kb__search`，直接呼叫一個 in-process MCP 工具。

loop 與 dispatch 都不變。MCP 只是往第 2 章的池裡加工具；第 3 章的 gate 讀取它們自我宣告的 annotation。

```bash
python sections/19-mcp-plugins-channels/src/test.py         # offline checks, no key
uv run python sections/19-mcp-plugins-channels/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code MCP transport](https://github.com/yasasbanukaofficial/claude-code)：
  `services/mcp/types.ts`（`TransportSchema`）、`client.ts`（`MCPTool` cloning、`buildMcpToolName`）、`normalization.ts`（`normalizeNameForMCP`）。
- [Claude Code MCP config and channels](https://github.com/yasasbanukaofficial/claude-code)：
  `config.ts`（precedence）、`channelNotification.ts`（`CHANNEL_TAG`），加上 `McpAuthTool`、`ListMcpResourcesTool`、`ReadMcpResourceTool`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/architecture.md`、`docs/cordis-primer.md`、`packages/acp/acp/README.md`、`packages/extensions/tool-cordis/README.md`。
- [Claude Code plugins](https://github.com/yasasbanukaofficial/claude-code)：`plugins/builtinPlugins.ts`、`plugins/bundled/`、`types/plugin.ts`，加上 `remote/` 與 `bridge/`。
- [Hermes Agent 原始碼](https://github.com/NousResearch/hermes-agent)：
  `mcp_serve.py`、`hermes_cli/plugins.py`（`PluginManager`、`VALID_HOOKS`）、`gateway/platforms/`、`gateway/platform_registry.py`、`plugins/platforms/`。
- [MCP specification 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28) 與它的
  [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)：stateless protocol、tools / resources / prompts 三種 primitive、
  `server/discover`、`subscriptions/listen`、MRTR、deprecation 清單。
- MCP blog：[the future of transports](https://blog.modelcontextprotocol.io/posts/2025-12-19-mcp-transport-future/)（protocol 為什麼走向 stateless）、
  [SDK betas for 2026-07-28](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/)（v2 SDK 與向後相容）。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter4.md`，以中文原版為準。工具生態那一節：
  MCP 的 primitive、公告 schema 的 context 開銷，以及信任模型（描述投毒、工具遮蔽、被劫持的更新、憑證範圍）。
- 章節定位：[learn-claude-code · s19_mcp_plugin](https://github.com/shareAI-lab/learn-claude-code)。
