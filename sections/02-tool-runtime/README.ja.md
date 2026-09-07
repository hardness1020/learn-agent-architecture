# 2 · Tool runtime

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 能力を足すとは、tool を登録することです。loop はそのままです。

agent loop は tool を通してしか行動できません。モデルは `name` と `input` を持つ構造化された `tool_use` ブロックを出力します。

harness はその名前をコードに対応づけます。入力を検証し、ハンドラを実行し、結果を返します。

runtime は次のことをしなければなりません。

1. どんな tool があるかをモデルに伝えます。
2. 各 tool の入力 schema を説明します。
3. 各 `tool_use` を名前で振り分けます。
4. 可能なときは安全な呼び出しを並列に実行します。
5. 大きな tool カタログでも見つけられる状態を保ちます。

この層がなければ、モデルは行動を要求できても、その行動を実行するものが何もありません。

`bash` tool 1 つしかない場合、どんな能力も文字列の処理になります。tool ごとの検証も permission のロジックもありません。

2 種類の失敗がここで生まれ、モデルのせいにされます。2 つの説明が重なっているせいでモデルが間違った tool を選ぶこと。harness が入力を書き換えたせいで編集が失敗すること。

---

## 仕組み

![Mechanism diagram](assets/02-tool-runtime.png)

tool は、名前、ハンドラ、schema、いくつかの述語を持つ小さなオブジェクトです。registry が tool を名前で保存します。dispatch はその引き当てです。

### 新規: tool runtime

```python
@dataclass
class Tool:                                  # src/tools.py
    name: str
    run: Callable[[dict], Any]
    description: str = ""                      # advertised to the model
    input_schema: dict = ...                   # the Anthropic schema it accepts
    is_read_only: bool = False
    is_concurrency_safe: bool = False         # may batch in parallel
    is_edit: bool = False                     # read by the gate (section 3)

class Registry:                              # src/tools.py
    def register(self, tool): self._tools[tool.name] = tool   # add a handler
    def get(self, name):      return self._tools.get(name)    # dispatch = lookup
    def schemas(self):        ...             # the tools list handed to the model
```

- tool は dataclass です。
- registry は `name -> tool` です。
- 能力を足すとは、ハンドラを 1 つ登録することです。
- `schemas()` は、モデルに提示する tool の一覧を返します。
- `run_concurrently` は `is_concurrency_safe` が付いた tool をまとめて実行します。
- 安全でない呼び出しは順番のままなので、書き込みが競合しません。

### 組み込み方

セクション 1 では `HANDLERS` の dict をその場に書いていました。セクション 2 では `registry` を loop に渡し、各 `tool_use` を `_dispatch` 経由で振り分けます。

```python
def run_turn(messages, model, registry, max_steps=10): # src/loop.py (now takes a registry)
    ...
    results = [_dispatch(b, registry)                   # was: run_tool(call)
               for b in response.content if b.type == "tool_use"]
    messages.append({"role": "user", "content": results})

def _dispatch(block, registry):              # resolve, run, wrap as a tool_result
    tool = registry.get(block.name)           # name -> tool
    content = run_tool(tool, block.input)
    return {"type": "tool_result", "tool_use_id": block.id, "content": content}
```

loop の本体はそれ以外変わりません。dispatch の段だけが registry を使うようになります。

`_dispatch` が次の拡張点です。セクション 3 はそこに permission のゲートを足します。セクション 4 はそこに hook を足します。

デモは分かりやすさのために逐次で dispatch します。実際の runtime は安全な呼び出しをまとめ、大きな tool schema を必要に応じて読み込みます。

### さらに読む

ここから先は `src/` にはありません。ai-agent-book と tool 利用に関する公開された研究から来ていて、表に挙げたシステムで確認が取れているわけではありません。
Claude Code を名指ししている箇所は、その対比だけが自身のソースに基づいています。

**分類。** tool は、呼び出しがどこへ向かい何に触れるかで、5 つのグループに分かれます。

- **知覚** は外の世界を読みます。
- **実行** は外の世界を変えます。
- **協働** は別の agent に届きます。
- **イベント起動** は、外の世界が agent を起こせるようにします。
- **ユーザーとの対話** は人に届きます。

5 つのうち 4 つには、後で専用のセクションがあります。セクション 6、12、16 が協働を組み立てます。セクション 13 と 14 がイベント起動を組み立てます。
セクション 19 がユーザーの channel を組み立てます。このセクションが、5 つすべての土台になる層を組み立てます。
この分類が役に立つのは契約が違うからです。知覚の呼び出しは安全に繰り返せてまとめられますが、実行の呼び出しはそうはいきません。

**粒度。** agent が PDF、Word ファイル、表計算を読む必要があるとします。tool は 1 つか、3 つか。

同じ種類の入力に同じ処理をするなら統合してください。型を引数に取る `read_document` 1 つのほうが、ほとんど同じ reader を 3 つ並べるより選びやすくなります。
引数が重ならなくなったら分割してください。無関係なフィールドを合成した schema は、どのフィールドが効くのかを示せないので、モデルは違うフィールドを埋めます。

**description の作り込み。** `description` は、モデルが tool を選ぶ前に読む唯一のテキストです。人間向けのドキュメントではありません。

役に立つ description は 5 つのことを押さえます。

- その tool をいつ使うか。
- いつ使わないか。
- 各引数の実際の値。
- 返ってくるものの形。
- 呼び出しにかかるコスト。

作り込んだ例をいくつか載せるほうが、散文をもう 1 段落足すより効きます。本は、例を足すと大きく改善したと報告しています。
その数値には出典が付いていないので、幅ではなく方向として受け取ってください。

**引数をそのまま通す。** モデルが、検索文字列に丸い引用符を含む編集を送ります。ハンドラに届くまでの間に、harness がそれをまっすぐな引用符に直します。

編集は空振りし、モデルには文字列が一致しなかったとしか見えません。モデルは正しい入力を送っているので、transcript にはそのずれを説明するものが何もありません。
だから規則はこうなります。入力は手を加えずにハンドラへ渡してください。おかしな入力は理由を添えて拒否してください。決して書き換えず、モデルが書いていない引数を足さないでください。

**チェックリスト引数。** 返金の tool が `expected_price` を受け取りますが、ハンドラはそれを一切使いません。

書かせること自体が目的です。呼び出しが走る前に、モデルは自分が信じている価格を明示しなければなりません。
ハンドラは保存されている価格を読んでそれで判断し、2 つが食い違ったら両方を記録します。
こうして最後の検査は、モデルが偽装できないデータの上に立ちます。τ-bench も同じやり方で採点します。agent が何をしたと言ったかではなく、最終的なデータベースの状態を読みます。

**知覚のインターフェース。** 大きなリポジトリの検索が 4000 行にヒットしますが、context に入るのは最初の 50 行だけです。

結果を正直に保つ規則が 3 つあります。

- 検索は候補 1 ページ分とカーソルを返します。
- 読み取りは offset と limit を取り、モデルが長いファイルを歩けるようにします。
- 切り詰めは結果の中に明示します。

黙って切るのはエラーより悪いです。モデルは途中までのファイルを全体だと思って読み、以降のすべての手順がその欠落を引き継ぎます。

コード検索がこの選択をよく表します。やり方は 4 つあり、1 つだけを使うシステムはありません。

| やり方 | 見つかるもの | コスト |
| --- | --- | --- |
| **Glob** | パターンに合うパスのファイル。 | 中身については何も分かりません。 |
| **Grep** | 完全一致の文字列と正規表現。行番号付き。 | 絞り込みに何回かの呼び出しが要ります。同義語は取りこぼします。 |
| **埋め込みインデックス** | 意味によるコード。平易な言葉のクエリでも当たります。 | インデックスを構築し同期し続ける必要があります。順位付けは不透明です。 |
| **LSP のシンボル** | 定義、参照、型を正確に。 | 言語ごとに language server が要ります。 |

Claude Code と Cursor は、この表の両端にいます。Claude Code はインデックスを持たず、段階を踏んで検索します。glob、次に grep、次に read という順で、
モデルが呼び出しの合間にクエリを絞ります。本は、Cursor がその代わりにコストを払ってインデックスを構築していると述べています。そうすることで、平易な言葉のクエリが
識別子を 1 つも含まないコードを見つけられます。

編集も同じように分かれます。何が変わったかを伝える言い方は 5 つあります。

| 方式 | モデルが出力するもの | トレードオフ |
| --- | --- | --- |
| **diff と適用モデル** | 大まかな骨組みの diff。2 つめの訓練済みモデルが書き直します。 | 速くて融通が利きます。その 2 つめのモデルが要ります。 |
| **旧文字列と新文字列** | 探す正確なテキストと、その場所に入れるテキスト。 | 曖昧さがなく、失敗すればはっきり分かります。事前に読み直す必要があります。 |
| **行番号** | 範囲と、その置き換え。 | 小さく収まります。先の編集でファイルがずれると古くなります。 |
| **エディタコマンド** | vim 風の小さなコマンド言語。 | 簡潔です。間違えうる文法が 1 つ増えます。 |
| **アンカー** | 開始マーカーと終了マーカー。 | ずれても生き残ります。マーカーが繰り返されると曖昧になります。 |

同じ 2 つのシステムが、編集でもまた分かれます。Claude Code は正確な旧文字列を置き換え、モデルに先にファイルを読ませます。
そのため古い文字列は、違う行を編集する代わりにはっきり失敗します。本は、Cursor が代わりに大まかな骨組みを送り、
2 つめの訓練済みモデルがそこからファイルを書き直すと述べていて、その経路のほうが速いと報告しています。

**早期開始と連鎖中断。** 呼び出しはバッチの残りを待つ必要がありません。自分の引数の解析が終わった瞬間に開始できます。

モデルはまだ後続の呼び出しを書いている最中なので、早く始めた呼び出しの待ち時間は生成の裏に隠れます。それで速度が稼げますが、失敗時の規則が 1 つ要ります。
エラーは、それに依存していた呼び出しを止めます。同じバッチの独立した呼び出しは走り続け、親の turn も走り続けます。

**shell の状態。** ある呼び出しが `cd build` を実行し、続けて仮想環境を有効化します。次の呼び出しはそのどちらも見えるでしょうか。設計は 2 通りあり、どちらにも理由があります。

- **呼び出しごとにリセット。** Claude Code の bash tool は呼び出しの間に生きた shell を保持しません。ある呼び出しで設定した変数や shell 関数は次の呼び出しでは消えていて、
  tool の説明はモデルに絶対パスを使うよう伝えます。どの呼び出しも単独で再現でき、並列の呼び出しが互いに漏れ込むこともありません。
- **1 つの永続 session。** 本は共有ターミナルを既定にしていて、`cd`、export した変数、有効化した仮想環境がすべて残ります。
  並列作業のために別の shell も使えます。モデルが繰り返す準備コマンドは減り、harness は追跡してリセットすべき session の状態を抱えます。

**規模が大きいときの発見。** 20 台の接続済みサーバーが数百の tool を提供し、その完全な schema は prompt に収まりません。

そこで registry はまず名前だけを送り、完全な schema は求められたときにだけ読み込みます。その要求は、平易な言葉でモデルから来ることもあります。
MCP-Zero では、agent が自分に足りない能力を述べ、それをサーバーに、次にそのサーバー上の tool に対応づけ、一致した schema だけを注入します。
モデルはその tool の存在を知っている必要がまったくありません。これはキーワード検索にはできないことです。

**cache を壊さない読み込み。** 読み込んだ schema が context のどこに置かれるかで、コストが決まります。

一度だけ末尾に追記して、そのまま置いておいてください。prompt の先頭にある tool のブロックを編集すると、cache 済みの前置きと、それ以降のすべての token が無効になります (セクション 10)。
追記なら前置きはそのままで、schema は次の turn には普通の履歴になります。

---

## システム別

各 agent が tool をどう定義し、呼び出しをどう振り分け、並列性をどう扱い、大きなカタログをどう見せるかです。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | tool ごとの検証、permission、並列実行、遅延した発見。 | `bash` tool 1 つ、小さな runtime、カタログなし。 | agent ごとの tool セット。呼び出しごとに監査された 1 本のパイプライン。 |
| **欠点** | どの tool も契約を背負う必要あり。 | tool ごとの検証も permission もなし。ゲートに見えるのはコマンド文字列 1 本のみ。 | 単純な tool でも出力の契約の宣言が必須。 |
| **理由** | 能力 1 つにつき新しい tool 1 つ。loop はそのまま。 | どの行動も shell コマンドなので、tool は 1 つで十分。 | スコープごとの解決器 1 つが、引き当て、dispatch、表示を担当。 |
| **方法: tool definition** | schema、ハンドラ、述語。 | `bash` schema 1 つとコマンドのフィールド 1 つ。他の名前はエラー。 | schema、型付きの出力契約、本体、副作用のない表示器。 |
| **方法: dispatch** | permission で絞ったプールに対する別名の引き当て。MCP 込み。 | registry なし。どの呼び出しも shell コマンド。 | スコープ付きの引き当て、続いて 5 段階の防御付きパイプライン。 |
| **方法: parallel calls** | 安全な呼び出しはまとめ、安全でないものは単独で実行。フラグは既定でオフ。 | なし。テキストモードは応答ごとに行動 1 つ。 | 呼び出しごとに分類し、判断がつかなければ排他側に固定。 |
| **方法: discovery** | まず名前。完全な schema は名前かキーワードでの要求時に読み込み。 | tool が 1 つなら不要。 | 遅延読み込みはなし。制限とプリセットが各スコープを形成。 |

---

## 失敗モード

- **未知の tool 名。** モデルが存在しない tool や無効な tool を指名します。loop を落とさず、`tool_result` にエラーを返してください。
- **schema のずれ。** schema の内容とハンドラの期待が食い違います。dispatch の前に検証してください。
- **安全でない並列実行。** 2 つの書き込みが同じファイルを壊しえます。安全と分かっている tool 以外は逐次実行を既定にしてください。
- **カタログのあふれ。** tool schema が多すぎると prompt を圧迫しえます。完全な schema は必要になるまで遅らせ、読み込んだ schema は末尾に追記して cache 済みの前置きを守ってください。
- **結果が大きすぎる。** 大きな出力は context 窓を埋めえます。結果に上限を設け、完全な出力は保存し、プレビューとパスを返してください。
  切り詰めは明示してください。黙って切ると、モデルは途中までのファイルを全体だと思って読みます。
- **間違った tool が選ばれる。** 2 つの説明が重なっているか、1 つの tool が 2 役をこなしています。重複を統合し、詰め込みすぎた schema を分割し、各 tool が何のためでないかを書いてください。
- **入力が黙って変わる。** harness がハンドラへの途中で引数を正規化したり足したりします。呼び出しは失敗し、モデルには理由が分かりません。おかしな入力は理由を添えて拒否してください。
- **バッチの失敗が広がる。** 並列バッチ内の 1 つの失敗が turn 全体を落とします。中断するのは、それに依存していた呼び出しだけにしてください。

---

## 実行

[`src/`](src/) は 01 を引き継ぎ、次を足します。

- [`tools.py`](src/tools.py): `Tool`、`Registry`、`run_concurrently`。
- [`loop.py`](src/loop.py): 各 `tool_use` を `Registry` 経由で振り分けます。
- [`demo.py`](src/demo.py): `ReadFile` tool を登録し、API に対して loop を走らせます。
- [`test.py`](src/test.py): dispatch、未知 tool のエラー、並列バッチ実行を検査します。

```bash
python sections/02-tool-runtime/src/test.py         # offline checks, no key
uv run python sections/02-tool-runtime/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `Tool.ts`, `tools.ts`, `services/tools/toolOrchestration.ts`, `services/tools/toolExecution.ts`, `tools/ToolSearchTool/ToolSearchTool.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `models/utils/actions_toolcall.py`, `models/utils/actions_text.py`, `environments/__init__.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) の `dsh-v0.1.0-rc.7`:
  `docs/subsystems/tools.md`, `docs/tool-execution-pipeline.md`, `packages/core/tools/src/index.ts`, `packages/core/tools/src/schema.ts`。
- [learn-claude-code · s02_tool_use](https://github.com/shareAI-lab/learn-claude-code): セクションの組み立て方。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, `book/chapter5.md` (『深入理解 AI Agent』, 李博杰。中国語の原文が正典):
  tool の 5 分類、粒度、description の作り込み、引数をそのまま通すこと、知覚インターフェースの規則、能動的な発見、cache を壊さない読み込み、
  連鎖中断を伴う tool のストリーミング開始、永続 shell を既定にすること、検索と編集の比較、そしてチェックリスト引数。
  同書の Claude Code と Cursor の読み解きは、速く動く実装に対する著者自身のソース調査なので、その時点の証拠として読んでください。
- [MCP-Zero](https://arxiv.org/abs/2506.01056) (Fei ら): agent が能力の不足を宣言し、対応づけはまずサーバー、次に tool の順で走ります。
- [τ-bench](https://arxiv.org/abs/2406.12045) (Sierra): 成否は最終的なデータベースの状態で判定され、チェックリスト引数はそこに依拠しています。
