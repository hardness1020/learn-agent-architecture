# 1 · Agent Loop

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 1 つの loop が、モデルが答えるか tool を要求するまでモデルを呼び続けます。

素のモデル呼び出しは 1 回きりです。messages を送ると、応答が 1 つ返ります。

agent にはもう 1 段が要ります。モデルが要求した tool を実行し、結果を追記し、もう一度モデルを呼ばなければなりません。同じ `messages[]` が turn の間ずっと伸び続ける必要があります。

loop は次のことをしなければなりません。

1. 呼び出しをまたいで会話の状態を保ちます。
2. tool の使用と最終回答を見分けます。
3. 要求された tool を実行し、結果を追記します。
4. モデルが止まるまで、もう一度モデルを呼びます。

この loop がなければ、モデルは行動について推論できても行動できません。loop が間違っていれば、早すぎる段階で止まるか、永遠に走り続けるかのどちらかになります。

---

## 仕組み

![Mechanism diagram](assets/01-agent-loop.png)

1 つの `messages[]` の上に loop が 2 つあります。

チャット画面を思い浮かべてください。「台北の天気は。傘は持っていくべき。」と尋ねたとします。
モデルはまず天気の tool を呼び、その結果を見てから降水確率の tool を呼び、そのうえでようやく返事をするかもしれません。
**つまり 1 つの turn の中で、モデルは tool 呼び出しを挟みながら何度も呼び出されるのが普通です。**
あなたの質問から最終回答までのその一続きが inner loop、すなわち 1 つのユーザー turn です。
inner loop はモデルを呼び、`stop_reason` を調べ、必要なら tool を実行し、結果を追記し、モデルがこの turn の回答を出すまで繰り返します。

そのあと同じ画面で「明日は。」と尋ねます。これが新しい turn です。
outer loop は、turn を次々とつないで 1 つの会話にするものです。
新しい turn は同じ `messages[]` に追記されるので、モデルは「明日」に答えるときも、あなたが台北について尋ねたことを見ています。

inner loop は、呼び出し側が所有する `messages[]` の上での 1 turn です。

```python
def run_turn(messages, model, max_steps=10):        # src/loop.py · one turn over the shared messages[]
    for _ in range(max_steps):                       # the inner loop, with a backstop
        response = model(messages)                   # one Anthropic Messages call
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":       # model produced its answer for this turn
            return final_text(response)

        results = []                                 # tool_use: run each, feed back
        for block in response.content:
            if block.type == "tool_use":
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": run_tool(block.name, block.input)})
        messages.append({"role": "user", "content": results})

    raise RuntimeError("hit max_steps without end_turn")
```

- [`src/loop.py`](src/loop.py) の `run_turn()` が inner loop です。
- `messages` は Anthropic Messages 形式の共有状態です。
- `max_steps` は暴走した loop に対する安全上限です。
- `run_tool(name, input)` は tool を解決し、実行し、`tool_result` 用のテキストを返します。
- [`src/demo.py`](src/demo.py) の `model()` は `client.messages.create` の呼び出し 1 回です。loop は特定のプロバイダに依存しません。

outer loop は turn ごとにユーザーメッセージを 1 つ追記し、バッファを保ちます。

```python
messages = []                                        # src/demo.py · the conversation, owned by the caller
for user_text in turns:                              # the outer loop: one iteration per user turn
    messages.append({"role": "user", "content": user_text})
    reply = run_turn(messages, model)                # appends in place; turn N sees turns 1..N-1
```

loop を動かす `stop_reason` の値は 2 つです。

- `tool_use`: tool を実行し、結果を追記し、もう一度モデルを呼びます。
- `end_turn`: 最終回答を返します。デモは `tool_use` 以外のどの値でも停止します。

`messages[]` は、この session における会話の記憶そのものです。tool の結果もアシスタントの返答も、どちらもここに入ります。次のモデル呼び出しは、その全体の状態を踏まえて推論します。

この素朴な loop には permission のゲートがありません。セクション 3 が、tool の実行の前にそのゲートを足します。

---

## システム別

各 agent が loop をどう所有し、いつ止めるかをどう決めるかです。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | 進行のストリーミング、副作用のゲート、tool の並列実行。 | 極小の loop で、読むのも監査するのも簡単。 | 差し替え可能な loop、横取りできる各段階、再生できるログ。 |
| **欠点** | loop はより大きなランタイムの内側。 | 副作用のゲートも、ストリーミングも、並列 tool もなし。 | 可動部が最多。turn、step、inbox の語彙が必要。 |
| **理由** | 中心の分岐は 1 つに保ち、機能はその周りに追加。 | 最小の loop であること自体が狙い。完了を検出するのはモデルではなく環境。 | loop は対等な plugin のうちの 1 つ。 |
| **方法: loop driver** | 非同期ジェネレータ。tool は 1 つの契約で接続。 | while loop。モデルにコマンドを求めて実行。 | 永続的なイベントログの上に載る、差し替え可能な plugin。 |
| **方法: stop signal** | `stop_reason: end_turn`。 | 環境が提出マーカーを見つけ、`role: "exit"` を追記。 | 残務なし、checkpoint のブロックなし、または turn を終える結果。 |
| **方法: parallel tools** | あり。1 つのモデル turn 内の呼び出しは並列に実行可能。 | なし。行動は順番に実行。 | あり。排他的な呼び出しが障壁になり、安全な呼び出しは上限付きのプールを共有。 |
| **方法: streaming** | あり。モデルの token、tool 呼び出し、tool の結果を発生順に送出。 | なし。 | あり。ストリームの断片は永続イベントとして session log に記録。 |

---

## 失敗モード

- **停止条件がない。** バグや tool の loop が永遠に走りえます。ステップ数か token の上限を使ってください。
- **loop の途中で context があふれる。** `messages[]` は伸びる一方です。セクション 8 が context 管理を足します。
- **tool の一部が失敗する。** 失敗した tool も `tool_result` を返さなければならず、そうしてモデルが立て直せます。
- **結果が消える。** アシスタントの tool 呼び出しか tool の結果のどちらかを落とすと transcript が壊れます。両方を追記してください。

---

## 実行

[`src/`](src/) が連なりの起点で、次を含みます。

- [`loop.py`](src/loop.py): inner loop と共有の `messages[]`。
- [`demo.py`](src/demo.py): 2 turn の実デモ。turn 2 は turn 1 がバッファに残っていることに依存します。
- [`test.py`](src/test.py): tool の dispatch、最終テキスト、複数 turn の状態に対するオフライン検査。

セクション 2 から 11 はこの `src/` を引き継ぎ、`loop.py` を発展させながらセクションごとにファイルを 1 つ足していきます。

```bash
python sections/01-agent-loop/src/test.py         # offline checks, no key
uv run python sections/01-agent-loop/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `QueryEngine.ts`, `query/`, `Tool.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`, `exceptions.py`, `environments/local.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) の `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`, `docs/agent-lifecycle.md`, `docs/subsystems/core.md`, `packages/core/agent-loop/src/agent.ts`, `packages/core/agent/src/types.ts`。
- [learn-claude-code · s01 Agent Loop](https://github.com/shareAI-lab/learn-claude-code): セクションの組み立て方。
