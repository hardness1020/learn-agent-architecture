# 19 · MCP / plugins / channels

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 能力が足りない場合は、差し込んで増やします。harness は 1 つの標準 protocol を通じて外の世界へ手を伸ばします。

harness にできるのは自分の tool が許すことだけで、組み込みの tool はどれも事前に定義されています。入力スキーマ、実行、エラー処理、そのすべてがです。

これでは、ユーザーが使いたいサービスの数に追い付きません。issue tracker、デプロイシステム、ナレッジベースなどです。そのそれぞれについて、使われている言語ごとに tool を手書きすることはできません。

MCP (Model Context Protocol) は、その差を埋める公開された契約です。外部のサービスが自分の tool を宣言し、agent は誰が書いたのかもどう作られたのかも知らないまま、それを呼びます。
MCP の用語では、そのサービスがサーバーで、接続して呼び出す harness がクライアントです。

こうして、誰も harness を編集しないまま、agent は Jira の tool やデプロイの tool を手に入れます。MCP を省くと、能力はバイナリに入ってリリース済みのものに固定されます。

MCP の上には、さらに 2 つの部品が載ります。plugin はサーバーを hook や skill とまとめ、1 つの単位としてインストールできるようにします。
channel はサーバーの側からメッセージを押し込めるようにします。どちらも同じ protocol に乗ります。

---

## 仕組み

![Mechanism diagram](assets/19-mcp-plugins-channels.png)

各サーバーへ接続し、その tool を発見し (`tools/list`)、それぞれをランタイムの `Tool` (section 2) として包み、loop が dispatch するのと同じプールへ混ぜます。

名前は `mcp__<server>__<tool>` と名前空間で区切られるので、2 つのサーバーが衝突することはありません。loop とゲートは変わりません。MCP の tool とは、`run()` がトランスポート越しに外へ呼び出す `Tool` です。

- 発見はサーバーごとに `tools/list` を 1 回呼ぶだけです。返ってきた仕様 1 つが、包まれた `Tool` 1 つになります。
- 名前は名前空間で区切られ、正規化されるので、一意になり、API の名前パターンにも合います。
- 各 tool の MCP アノテーション (`readOnlyHint`、`destructiveHint`) は、ゲートが読む permission のヒントになります (section 3)。
- 1 つの `Registry` に混ぜられるので、モデルは MCP の tool と組み込みの tool を同じ一覧の中で見ます。

### その下の通信 protocol

2026-07-28 の仕様改訂で、通信路上はステートレスになりました。いまはどのリクエストも単独で成立するので、サーバーのどのレプリカでも答えられます。
上に書いた harness 側 (発見、包む、混ぜる) は変わりません。変わったのは通信路上です。

- **ハンドシェイクの廃止。** 以前は、クライアントが `initialize` を呼び、それが済むまで他のことをしませんでした。
  いまはどのリクエストが最初でもかまいません。各リクエストが自分の protocol バージョンと capability を `_meta` に載せます。
  先にバージョンを確認したいクライアントは `server/discover` を呼びます。
- **session の廃止。** 以前は、サーバーが session ヘッダーの裏で接続ごとの状態を保っていました。
  いまは、呼び出しをまたいで状態が必要なサーバーはハンドルを返し、クライアントはそれを普通の tool の引数として返します。
- **通知ストリームの一本化。** 以前は、変更を知るためにクライアントが長い GET 接続を開いたままにしていました。
  いまは `subscriptions/listen` のストリームを 1 本開き、欲しいイベント (tool 一覧の変更、リソースの変更) を指定します。
  一覧の結果には `ttlMs` フィールドも付き、クライアントがどれだけキャッシュしてよいかを示します。
- **サーバーは折り返し呼び出すのではなく、返信で尋ねる。** 以前は、tool 呼び出しの途中でサーバーが自分からクライアントへリクエストを送れました
  (ユーザーへ質問する、モデルにサンプリングさせる)。いまは `input_required` の印を付けた中間結果を返し、
  クライアントは答えを添えて同じリクエストをやり直します。
- **機能の削減。** Roots、Sampling、Logging、そして旧来の HTTP+SSE トランスポートは非推奨になりました。
  公式に残るトランスポートは 2 つです。ローカルのサーバー向けの stdio と、リモート向けの Streamable HTTP です。

agent を使う人にとって、画面の上では何も変わりません。古いサーバーは動き続けますし、v1 の SDK も保守が続きます。
効いてくるのは下の層です。リモートのサーバーはロードバランサの背後でスケールし、最初の呼び出しは往復を 1 回省け、キャッシュされた tool 一覧は token を節約します。
非推奨の機能を使っているサーバーには、移行のために 12 か月の猶予があります。その作業はユーザーではなく、サーバーの作者が負います。

### 新規: 発見した tool を包む

`mcp.py` は、発見した仕様 1 つ 1 つを `Tool` に変えます。名前はサーバーどうしが衝突しないよう名前空間で区切られ、API の文字集合に合わせて正規化されます。

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

- `tool_name` はすべての tool を名前空間で区切ります。`normalize` は `[a-zA-Z0-9_-]` の外の文字を `_` に置き換え、API の名前パターンを満たします。
- `run` は素の tool 名とサーバーの `call` を閉じ込めるので、包まれた `Tool` を dispatch すると、トランスポート越しに元へ届きます。
- `readOnlyHint` アノテーションは `is_read_only` になり、permission のゲート (section 3) は許可か確認かを決めるためにそれを読みます。

### 新規: 発見と統合

`connect` は発見を 1 回走らせ、包まれた tool を返します。呼び出し側はそれを loop の `Registry` へ混ぜます。

```python
def connect(server, conn):                             # src/mcp.py
    return [wrap(server, spec, conn.call) for spec in conn.list_tools()]
```

- `conn` は生きたトランスポートです。本番では `stdio` か `http`、デモではプロセス内のものです。発見はどれかを気にしません。
- 返ってきた `Tool` は組み込みと同じプールへ登録されるので、`registry.schemas()` は両方をまとめて広告し、loop も同じやり方で dispatch します。

### 新規: channel と plugin の設定

小さめの部品が 2 つ、このセクションを締めくくります。

1 つ目はメッセージの流れを逆向きにします。通常は agent がサーバーを呼びますが、サーバーの側から自分でメッセージを押し込むこともできます (Slack のメッセージが届いた場合など)。
harness はそのテキストを `<channel>` タグで包み、agent の次の turn の前に置くので、モデルがそれを読みます。

```python
def wrap_channel(source, payload):                     # src/mcp.py
    return f'<{CHANNEL_TAG} source="{source}">{payload}</{CHANNEL_TAG}>'
```

2 つ目は設定の重ね合わせです。同じサーバーが plugin、user、project の設定に同時に定義されることがあります。`merge_servers` は優先順位で勝者を選びます。

```python
def merge_servers(*layers):                            # src/mcp.py
    merged = {}
    for scope in PRECEDENCE:                            # plugin < user < project < local
        for layer in layers:
            merged.update(layer.get(scope, {}))
    return merged
```

- `wrap_channel` は Slack、Discord、SMS を、同じ protocol の上の双方向のインターフェースに変えます。タグで包まれたブロックは、バックグラウンドの通知と同じようにキューへ入ります (section 13)。
- `merge_servers` は複数のスコープに定義されたサーバーを解決します。`local` が `project` を上書きし、`project` が `user` を上書きし、`user` が `plugin` を上書きします。

channel には誰でも送れます。届いた Slack や SMS のメッセージが、必ずしもユーザーからとは限りません。スパムかもしれませんし、agent を誘導するための指示かもしれません。
そこで、turn になる前にゲートを通します (hermes-agent は認証の前に、届いたメッセージすべてに対して `pre_gateway_dispatch` を発火します)。

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

- ゲートは、loop がテキストを見る前に、破棄する (スパム、未知の送信者) か、書き換える (マスキング) ことができます。
- `None` を返せば turn はまったく起きません。ゴミ入力に対して、いちばん安上がりな結末です。

### 組み込み方

デモはサーバーを 1 つ発見し、agent の turn を 1 回走らせます。モデルは MCP の tool を、中身を知らないまま呼びます。

```python
reg = Registry()
for t in mcp.connect("kb", KBServer()):                # discover, wrap, merge
    reg.register(t)
run_turn([...goal...], model, reg, Session(mode=DEFAULT))   # the one agent call
```

- モデルは tool 一覧の中で、組み込みの tool と並んだ `mcp__kb__search` を見て、それを呼びます。誰がその tool を書いたのかを知ることはありません。
- この tool は読み取り専用なので、ゲートは確認を出さずに許可します。破壊的な tool なら確認を出すか、修飾された名前を条件にしたルールで事前に許可されます。
- loop は変わりません。MCP はプールへ tool を足すだけで、その先はすべて section 2 の dispatch と section 3 のゲートです。

### さらに読む

ここから先は `src/` にはありません。ai-agent-book と MCP の仕様に基づく内容で、表に挙げたシステムでの裏付けは取れていません。

**3 つのプリミティブ、1 つのプール。** サーバーが提供できるものは 3 種類あります。上のプールに届くのは tool だけです。

- **Tools** は操作です。モデルが 1 つ選んで呼びます。`tools/list` が返すのはこれで、上のコードが包むのもこれです。
- **Resources** はクライアントが読めるデータで、それぞれに URI が付きます。ファイル、テーブル、wiki のページなどです。クライアントが 1 つ取得し、そのテキストを context へ入れます。モデルが呼ぶことはありません。
- **Prompts** はサーバーが渡してくるテンプレートです。たいていはユーザーが実行するコマンドとして現れ、モデルが選ぶものとしては現れません。

**resource は tool 一覧に載せません。** Claude Code は resource を 1 つずつ広告しません。tool を 2 つ用意し、片方が resource を一覧し、もう片方がそれを読みます。
だから、1000 件の文書を持つサーバーでも、tool 一覧では 2 エントリで済みます。

**接続と広告は別々の判断です。** サーバーへ接続すれば相互運用性が手に入ります。その tool を広告すると context を消費します。
1 つ目だけをやって、2 つ目は全部やらない、という選び方ができます。

**広告の代償。** 広告した tool は、リクエストのたびに token を消費します。名前、説明、そして入力スキーマの全体が、タスクの手前に載ります。
サーバー 5 つで、タスク本体より多いテキストになることもあります。一覧が長いと、モデルが誤った tool を選ぶ頻度も上がります (section 2)。

**選べる 3 つの水準。** どれだけ広告するかは、全サーバー一括ではなく、サーバーごとに決めます。

- **全部。** いちばん単純です。session がほぼ毎 turn 使うサーバーに向きます。
- **索引。** 名前と 1 行の要約だけを広告します。モデルがその tool を求めた時点で、完全なスキーマを読み込みます (発見の側は section 2 で扱っています)。
- **入口を 1 つ。** サーバー名と tool 名を受け取る tool を 1 つだけ広告します。残りはその裏に置きます。agent が払うのはスキーマ 1 つ分で、50 個分ではありません。

**これらは protocol には含まれません。** 仕様が定めるのは、tool をどう一覧し、どう呼ぶかまでです。そのうち何個が prompt に届くかは、クライアント次第です。
つまり遅延読み込みは、自分の harness で確認すべき設定です。サーバーの側は、それが有効だと決めてかかることはできません。

---

## システム別

harness が自分の外側へどう手を伸ばすかを示します。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | どのサービスでも、どの言語でも、harness の編集は不要です。 | 他のクライアントが、これを MCP サーバーとして動かせます。 | サーバーは設定なので、再起動なしで差し替えられます。 |
| **欠点** | 攻撃面が増え、しかもアノテーションはサーバーの自己申告です。 | channel には誰でも送れます。スパムや誘導のテキストも来ます。 | tool だけで、メッセージを押し込むチャットの channel はありません。 |
| **理由** | MCP がなければ、能力はリリース済みのものに固定されます。 | agent は MCP のクライアントであり、同時にサーバーでもあります。 | すべてが plugin なので、サーバーも plugin の 1 つです。 |
| **方法: トランスポート** | stdio からリモートの http まで 6 種類を、別々のプールで持ちます。 | MCP を双方向で使い、加えてチャットのアダプタも持ちます。 | ローカルの stdio とストリーミング http で、サーバー 1 つにつき plugin 1 つです。 |
| **方法: plugin の形式** | plugin がサーバー、hook、skill をまとめ、優先順位で統合されます。 | マニフェストと、登録用のエントリです。 | 設定の行です。パッチが id で 1 行を置き換えます。 |
| **方法: tool プールの組み立て** | 複製して名前空間で区切り、アノテーションがゲートへ渡ります。 | plugin と MCP の tool が 1 つの registry に入ります。 | サーバーの tool は 1 セットとして差し替わるか、ロールバックします。 |

---

## 失敗モード

- **名前の衝突。** 2 つのサーバーがどちらも `search` を公開。`mcp__server__tool` の名前空間が衝突を防ぐ。ただしサーバー名に `__` があると解析を誤るので、名前は単純に保つ。
- **tool 一覧の肥大化。** サーバーが多いと tool 一覧が大きくなり、token を消費し、選択も混乱します (section 2)。
  緩和策: 説明を切り詰め、毎回すべてのスキーマを送るのではなく、どれだけ広告するかをサーバーごとに決める。
- **接続後の古いプール。** session の途中で追加したサーバーはキャッシュされた tool 一覧にないので、モデルからは見えません。
  緩和策: 変更時にプールと prompt を作り直す (section 8)。2026-07-28 の仕様は、このために `subscriptions/listen` 上の `toolsListChanged` と `ttlMs` のヒントを追加しています。
- **接続の不安定さ。** 不安定なサーバーは timeout し、接続がリセットされ、トークンが失効します。緩和策: 連続で失敗したら再接続し、`401` で再認証し、呼び出しごとに timeout を置く (section 11)。
  ステートレス改訂ではストリームの再開性がなくなったので、途中で壊れたリクエストは再開ではなく、新しいリクエストとして出し直します。
- **副作用を信用しすぎる。** サーバーが破壊的な tool に `readOnlyHint: true` を付け、確認を飛ばします。緩和策: 修飾された名前へのルールで、それでもゲートする (section 3)。
- **説明文の汚染。** tool の説明はサーバーが書いたテキストで、モデルはそれを指示として読みます。
  サーバーはそこに命令を潜ませられます。たとえば、まずユーザーのキーファイルを読んで一緒に送れ、といった命令です。モデルは実行してしまうかもしれません。
  緩和策: サーバーを入れる前に説明を読む。説明が変わったら、変更されたコードと同じようにレビューする。
- **tool の乗っ取り。** すべてのサーバーが 1 つの prompt を共有します。だからあるサーバーの説明が、別のサーバーの tool について語り、
  決済用の tool は壊れていると主張して、呼び出しを自分に引き寄せることができます。
  緩和策: 名前空間は名前の衝突を止めます。これは止めません。レビューしていないサーバーは、本物の認証情報を持つ session に入れない。
- **更新の乗っ取り。** サーバーがレビューを通り、そのあと次回の起動で新しいコードと新しい説明を出してきます。protocol がユーザーに再確認することはありません。
  緩和策: バージョンを固定する。アップグレード後に説明を読み直す。サーバーごとに最小権限の認証情報を与え、悪いサーバーが他のサーバーのスコープへ届かないようにする。

---

## 実行

[`src/`](src/) は 18 を引き継いだうえで、次を追加します。

- [`mcp.py`](src/mcp.py): 発見と包み込み、plugin の設定の統合、channel の包み込み、そして受信側のゲート (`gate_inbound`)。
- [`test.py`](src/test.py): 発見と名前空間、ヒントの対応付け、ゲート込みのプール統合、設定の優先順位、channel のタグ、受信の破棄と書き換えを確認します。
- [`demo.py`](src/demo.py): agent の turn 1 回が、発見された `mcp__kb__search` を通じて、プロセス内の MCP tool を中身を知らないまま呼びます。

loop と dispatch は変わりません。MCP は section 2 のプールへ tool を足し、section 3 のゲートがその自己申告のアノテーションを読みます。

```bash
python sections/19-mcp-plugins-channels/src/test.py         # offline checks, no key
uv run python sections/19-mcp-plugins-channels/src/demo.py  # live demo, needs a key
```

---

## 出典

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
