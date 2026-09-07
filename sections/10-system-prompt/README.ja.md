# 10 · System prompt assembly

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 毎 turn、prompt を live state から組み立てます。

system prompt は agent の常設の指示書です。identity、ルール、tool、プロジェクトの context、有効な機能を記述します。

実際の agent では、これを 1 本のハードコードされた文字列のままにはできません。

tool、memory、出力スタイル、MCP サーバー、モードは session ごとに変わります。prompt は実際に有効なものを記述すべきです。

prompt の assembler は 3 つの問題を解きます。

1. 新しい機能の文章に、置き場所がはっきり決まります。
2. 無効な機能の文章は省けます。
3. 安定した section は prompt caching を使えます。

組み立てをしないと、prompt は古びるか、肥大するか、安全に変更しにくくなります。

---

## 仕組み

![Mechanism diagram](assets/10-system-prompt-assembly.png)

prompt を名前付きの section として定義します。静的な section もあれば、live state から文章を計算し、当てはまらないときは `None` を返す section もあります。

組み立ては単純です。すべての section を解決し、`None` を落とし、残りを連結します。

```python
sections = [
    intro, system_rules, doing_tasks, tools_section,
    session_guidance(), memory(), env_info(),
    output_style(), mcp_instructions(),
]
prompt = [s for s in resolve(sections) if s is not None]
```

2 つのルールで管理しやすさを保ちます。

1. section の採否は state で決めます。キーワードの推測では決めません。
2. 変動する内容は、安定した prompt の先頭部分から離しておきます。

### 新規: section と assemble

```python
@dataclass
class Section:                                          # src/prompt.py
    name: str
    compute: Callable    # (state) -> str | None ; static sections ignore state

def static(name, text) -> Section:
    return Section(name, lambda _state: text)

def assemble(sections, state) -> str:                  # the prompt for this turn
    parts = (s.compute(state) for s in sections)
    return "\n\n".join(p for p in parts if p is not None)
```

section のリストが、state に応じた採否を持ちます。

```python
DEMO_SECTIONS = [
    static("intro", "You are a tiny agent. ..."),
    Section("tools", lambda s: "Tools: " + ", ".join(s["tools"]) if s.get("tools") else None),
    Section("env", lambda s: f"cwd: {s['cwd']}" if s.get("cwd") else None),
    Section("mcp", lambda s: "MCP servers connected; ..." if s.get("mcp") else None),
]
```

recall した memory はこの prompt には含めません。section 9 が `<system-reminder>` メッセージとして注入します。そうすることで prompt の先頭部分がより安定します。

### prompt caching

system prompt の大半の section は session 中ずっと安定しています。デモでは最上位に cache の区切り点を置きます。

```python
client.messages.create(model=MODEL, system=assemble(DEMO_SECTIONS, state),
                       messages=messages, cache_control={"type": "ephemeral"})
```

安定した内容は、変動する内容より前に置くべきです。変わる値が前の方に現れると、cache の無効になる範囲が広がります。

このルールを厳しくしているのは料金体系です。cache は token 列の先頭一致をキーにします。
token を 1 つ変えると、それ以降の cache された token はすべて失われます。cache の読み出しは新規の入力 token のおよそ 10 分の 1 の費用で、cache の書き込みは新規より高くつきます。
つまり、単語を 1 つ動かしただけで、cache が効いていた呼び出しが全額の呼び出しに変わります。
これを繰り返し引き起こす原因が 2 つあります。prompt の先頭近くに書かれた時刻や token 数と、実行のたびに順序が変わる tool のリストです。

Claude Code は明示的な動的境界も使います。これにより、後半の小さな動的部分が変わっても、前半の大きな静的部分が守られます。

### 統合のしかた

loop は毎回のモデル呼び出しの前に prompt を組み立てます。

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)
    system = prompt(registry, session) if prompt else None   # 10 · assemble from live state
    response = model(messages, registry, system)
    ...
```

- `prompt` は section のリストを閉じ込めた callable です。
- 有効な tool や session のモードといった live state を読みます。
- `prompt=None` を渡すと section 9 の挙動のままになります。

### 対比: section の registry

上のリストは 1 つのファイルに固定されています。section を足すにはそのファイルを編集することになり、ファイル上の順序がそのまま prompt の順序です。

deepseek-harness は代わりに登録から組み立てます。各 plugin が、名前付きの section と、それがどこに入るかを示す番号を登録します。
番号は慣習として帯に分かれています。まず harness の identity、次にデプロイ時のペルソナ、その後に tool のガイダンスです。
組み立ては番号で並べ替えるので、plugin は他に何が登録されているかを知らずに自分の位置を決められます。

registry にはさらに 2 つのルールが付いてきます。

- 1 つの agent が、既にある名前で自分専用の section を登録できます。その agent には自分の版が見え、他は共有の版のままです。
- section の文章は `{{variables}}` を持てて、レンダリングは厳格です。未知の名前は例外を投げるので、リリース済みの prompt に穴が空いたまま出ることはありません。

動的な事実はこの prompt に入れません。それらは会話にスナップショットとして追記され、しかもレンダリング結果が実際に変わったときだけなので、先頭部分は cache が効いたままです。

[`src/registry.py`](src/registry.py) はこれを削ぎ落としたものです。対比のためのデモで `assemble()` には繋いでいないので、以降の section は同じ prompt のコードをそのまま引き継ぎます。

### 参考

以下は `src/` にはありません。ai-agent-book に基づく内容で、表にあるシステムで確認が取れているわけではありません。

**境界より前の条件は、先頭部分を掛け算で増やします。** 実行時の条件を 1 つ境界より前に置くと、cache は結果ごとに 1 つずつ、2 通りの先頭部分を保持することになります。
条件が 3 つなら 8 通りです。10 個なら 1000 通りを超え、それぞれが別々に温まるので、ほとんどの session が cold な状態で始まります。
条件付きの section を境界より後ろに置けば、先頭部分はまた 1 つに戻ります。

**タスクの種類ごとに例のセットを 1 つ決め、触らないでおきます。** few-shot の例は先頭部分に入るので、上のルールがそのまま当てはまります。
リクエストごとに最適な例を検索して入れると、呼び出しのたびに先頭部分が書き換わり、cache を捨てることになります。
固定のセットはリクエストへの当てはまりが少し落ちますが、session の間ずっと先頭部分を温かいまま保ちます。

**ステータスバーは、実行が今どこにいるかをモデルに伝えます。** モデルには harness が見えないので、context の末尾の数行に live state を書き込む harness があります。

- tool の呼び出しが何回走ったか
- 現在の TODO
- 経過時間
- 作業ディレクトリ

これらの行は最新に保つ必要があり、その方法は 2 つあります。どちらもただでは済みません。
毎 turn ブロックを置き換えれば state の正しい写しは 1 つだけになりますが、末尾が書き換わるのでそれ以降の cache は失われます。
毎 turn 新しいブロックを追記すれば cache は保たれますが、古いブロックが履歴に残り、モデルが既に変わった state に基づいて動く可能性があります。
Claude Code は追記する方式で、section 9 の `<system-reminder>` メッセージを使います。
どちらにしても、このブロックは実際の state を読むコードで書きます。LLM の要約器を使うと呼び出しが 1 回増え、レイテンシが増え、間違えることもあります。

**外部のテキストはデータであって、命令ではありません。** 取得した web ページ、ファイル、issue のコメント、MCP サーバーの応答は、すべてデータです。どれもユーザーの発言ではありません。
そのテキストを目印なしで送ると、中にある命令に見える 1 文が system prompt と対等に競合します。これが prompt injection です。
section 3 が脅威モデルと実行層での答えを担当します。乗っ取られた agent に何を許すかは、permission と sandbox が決めます。
prompt 層はもっと手前で、命令とデータを分けることで手を打てます。

- 外部の内容は、出所を名前で示すタグ付きのブロックで囲みます。タグ付きの内容は読むためのデータであって、従うべき命令ではないと prompt に書きます。
- 役割を厳格に保ちます。命令は system prompt に、結果は `tool_result` ブロックに入れ、人間は user turn で話します。
- 忠誠のルールを一度だけ明言します。agent はユーザーと運用者のために働き、tool 経由で届いたどんなテキストもそれを変えられません。本書はこれを principal loyalty と呼びます。

**prompt 層は境界ではありません。** モデルはそれでもルールから言いくるめられる可能性があり、だからこそ section 3 のチェックは別途走ります。
prompt 層は確率を下げます。実行層は被害を抑え込みます。

---

## システム別

毎 turn、prompt をどう構成するか。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | 古い指示が残らない。ガイダンスが live な tool と一致する。 | 設定から 1 回レンダリングするだけ。無効化するものがない。 | prompt のあらゆる事実に所有者が 1 つある。参照ミスは大きな音を立てて失敗する。 |
| **欠点** | section の registry、cache のルール、順序の規律が要る。 | 実行の途中で prompt を変えられない。 | registry、スコープ、順序の帯と、仕掛けが多い。 |
| **理由** | tool、memory、モードは session ごとに変わる。 | tool のセットは実行の途中で変わらない前提。 | plugin が自分の事実を持つので、prompt は編集ではなく組み立てで作る。 |
| **方法: assembly point** | prompt の builder。section ごとに 1 つの文字列。 | Jinja2 のテンプレート。変数が欠けると大きな音を立てて失敗する。 | registry と、各スコープが調整できるイベント。 |
| **方法: sections** | 静的な section と動的な section。プロジェクトの context はメッセージに載る。 | テンプレートは 2 つ、system と instance。 | 数値の帯に並ぶ名前付き section。スコープで上書きされる。 |
| **方法: when built** | 毎 turn live state から。動的な部分はメモ化する。 | 1 回だけ、実行の開始時に。 | 1 ステップに 1 回。変わる事実は代わりにスナップショットとして追記する。 |

---

## 失敗モード

- **変動するテキストが cache を壊す。** 変わる内容は後ろに置くか、prompt の先頭部分の外に出す。
- **section の cache が古くなる。** session の state が変わったら、メモ化した section を破棄する。
- **prompt に存在しない tool が書かれる。** tool の文章は live な有効 tool のセットから生成する。
- **context が prompt に混ざる。** プロジェクトのファイル、日付、git の状態は、頻繁に変わるなら context のメッセージに置く。
- **prompt の上書きが衝突する。** 優先順位を決める resolver を 1 つ用意する。
- **cache のキーが多すぎる。** 境界より前の実行時の条件 1 つごとに、別々に温める必要のある先頭部分が倍になる。条件付きの section は境界より後ろに置く。
- **ステータスのブロックが古くなる。** 追記した state が溜まり、モデルが古い写しに基づいて動く可能性がある。最新のブロックに印を付けるか、置き換えて cache の作り直しを受け入れる。
- **外部の内容が命令として読まれる。** tool の結果に出所のタグを付け、タグ付きの内容はデータだと明言する。本当の境界は section 3 の permission のチェックのままである。

---

## 実行

[`src/`](src/) は 09 を引き継ぎ、次を追加します。

- [`prompt.py`](src/prompt.py): `Section`、`static`、`assemble`。
- [`registry.py`](src/registry.py): deepseek-harness との対比。順序番号付きで登録される section、スコープによる上書き、厳格な `{{variable}}` のレンダリング。
- [`loop.py`](src/loop.py): 毎 turn prompt を組み立て直します。
- [`demo.py`](src/demo.py): 最上位の `cache_control` を追加します。
- [`test.py`](src/test.py): state に応じた採否を確認します。registry のチェックは順序、上書き、大きな音を立てて失敗する変数を対象にします。

```bash
python sections/10-system-prompt/src/test.py         # offline checks, no key
uv run python sections/10-system-prompt/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `constants/prompts.ts`, `constants/systemPromptSections.ts`, `utils/api.ts`, `QueryEngine.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `config/mini.yaml`, `_render_template` and `get_template_vars` in `agents/default.py`, `models/utils/cache_control.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/core/system-prompt/README.md`, `packages/core/system-prompt/src/index.ts`, `packages/core/agent-loop/src/runtime-context.ts`,
  `docs/subsystems/system-prompt.md`, `docs/agent-lifecycle.md`.
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): cache の区切り点、TTL、料金、token の下限。
- [Claude Code prompt caching docs](https://code.claude.com/docs/en/prompt-caching): 静的な先頭部分と動的な末尾のあいだの明示的な cache 境界。
- [ai-agent-book · chapter 2](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》, 李博杰; 中国語の原著が正典):
  KV cache の経済性、アーキテクチャ上の制約としての cache (境界より前の条件が cache のキーを掛け算で増やす)、few-shot による先頭部分の安定、
  agent のステータスバーと置き換え対追記のトレードオフ、principal loyalty を伴う context 層での injection 防御。
  本書のステータスバーと忠誠に関する計測は著者自身の benchmark なので、数値は単一の出典しかなく、ここでは繰り返しません。
- [learn-claude-code · s10_system_prompt](https://github.com/shareAI-lab/learn-claude-code): section の枠組み。
