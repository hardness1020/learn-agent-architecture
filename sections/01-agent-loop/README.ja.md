# 1 · Agent Loop

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 1 つのループは、応答するかツールを要求するまでモデルを呼び出し続けます。

生のモデル呼び出しはワンショットです。メッセージを送信すると、1 つの応答が返されます。

エージェントには別の手順が必要です。モデルが要求したツールを実行し、結果を追加して、モデルを再度呼び出す必要があります。同じ `messages[]` はターン全体で成長し続ける必要があります。

ループは次のことを行う必要があります。

1. 複数の通話にわたって会話状態を維持します。
2. tool use と最終的な答えを検出します。
3. 要求されたツールを実行し、結果を追加します。
4. 停止するまでモデルを再度呼び出します。

このループがないと、モデルはアクションについて推論できますが、行動することはできません。ループが間違っている場合は、停止が早すぎるか、永久に実行されます。

---

## メカニズム

![機構図](assets/01-agent-loop.png)

1 つの `messages[]` 上に 2 つのループがあります。

チャット ウィンドウを想像してください。 「台北の天気はどうですか？傘を持ったほうがいいですか？」と尋ねます。
モデルは、最初に天気ツールを呼び出し、次に結果を確認してから降雨確率ツールを呼び出し、その後にのみ応答することができます。
**そのため、モデルは 1 ターン以内にツール呼び出しを挟んで複数回呼び出されることがよくあります。**
あなたの質問から最終的な回答までの全体が内側のループ、つまり 1 ユーザー ターンです。
モデルを呼び出し、`stop_reason` をチェックし、必要に応じてツールを実行し、結果を追加し、モデルがこのターンの応答を返すまで繰り返します。

次に「明日はどうですか？」と尋ねます。同じウィンドウ内で。それは新たな展開です。
外側のループは、文字列が 1 つの会話に変わった後に変化するものです。
新しいターンはそれぞれ同じ `messages[]` に追加されるため、モデルが「明日」と答えても、台北について質問したことがわかります。

内側のループは、呼び出し元が所有する `messages[]` を 1 ターンオーバーします。

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

- [`src/loop.py`](src/loop.py) の `run_turn()` は内側のループです。
- `messages` は、Anthropic Messages 形式の共有状態です。
- `max_steps` は、暴走ループの安全限界です。
- `run_tool(name, input)` はツールを解決して実行し、`tool_result` のテキストを返します。
- [`src/demo.py`](src/demo.py) の `model()` は 1 つの `client.messages.create` 呼び出しです。ループは 1 つのプロバイダーに依存しません。

外側のループは、ターンごとに 1 つのユーザー メッセージを追加し、バッファを保持します。

```python
messages = []                                        # src/demo.py · the conversation, owned by the caller
for user_text in turns:                              # the outer loop: one iteration per user turn
    messages.append({"role": "user", "content": user_text})
    reply = run_turn(messages, model)                # appends in place; turn N sees turns 1..N-1
```

2 つの `stop_reason` 値がループを駆動します。

- `tool_use`: ツールを実行し、結果を追加し、モデルを再度呼び出します。
- `end_turn`: 最終的な答えを返します。デモは、`tool_use` 以外の値で停止します。

`messages[]` は、このセッションの会話メモリ全体です。ツールの結果とアシスタントの応答の両方がそこに含まれます。次のモデル呼び出しでは、その完全な状態について推論が行われます。

このベア ループには許可ゲートがありません。セクション 3 では、ツールの実行前にそのゲートを追加します。

---

## システムごと

各エージェントがループをどのように所有し、いつ停止するかを決定する方法。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **長所** |進行状況をストリーミングし、副作用を抑制し、ツールを並行して実行します。 |小さなループなので、読みやすく、監査も簡単です。 |スワップ可能なループ、インターセプト可能なフェーズ、再生されるログ。 |
| **短所** |ループは、より大きな runtime 内にあります。 |副作用ゲート、ストリーミング、並列ツールはありません。 |最も可動する部分。ターン、ステップ、受信トレイの語彙が必要です。 |
| **理由** | 1 つのコア ブランチを保持し、その周りに機能を追加します。 |ミニマルなループがポイントです。完了を検出するのはモデルではなく環境です。 |ループはピア間の 1 つの plugin です。 |
| **方法: ループ ドライバー** |非同期ジェネレーター。ツールは 1 つのコントラクトを通じてプラグインされます。 | while ループ。モデルにコマンドを要求し、実行します。 |耐久性のあるイベント ログ上で交換可能な plugin。 |
| **方法: 信号を停止します** | `stop_reason: end_turn`。 |環境は送信マーカーを認識し、`role: "exit"` を追加します。 |支払うべきものは何もなく、チェックポイントブロックもターンエンドの結果もありません。 |
| **方法: 並列ツール** |はい。 1 つのモデル ターン内の呼び出しは並行して実行できます。 |いいえ。アクションは順番に実行されます。 |はい。排他的呼び出しは障壁を形成します。安全な呼び出しは、制限されたプールを共有します。 |
| **方法: ストリーミング** |はい。モデル トークン、ツール呼び出し、ツールの結果が発生したときに生成されます。 |番号 |はい。ストリーム チャンクは永続イベントとしてセッション ログに記録されます。 |

---

## 障害モード

- **停止条件なし。** バグまたはツールのループは永久に実行される可能性があります。最大ステップまたはトークン制限を使用します。
- **ループ中のコンテキスト オーバーフロー。** `messages[]` は増加するだけです。セクション 8 では、context management を追加します。
- **部分的なツールの障害。** モデルが回復できるように、障害が発生したツールでも `tool_result` を返す必要があります。
- **結果が失われます。** アシスタント ツールの呼び出しまたは tool result を削除すると、トランスクリプトが中断されます。両方を追加します。

---

## 実行可能

[`src/`](src/) は次のようにチェーンを開始します。

- [`loop.py`](src/loop.py): 内部ループと共有 `messages[]`。
- [`demo.py`](src/demo.py): 2 ターンのライブ デモ。ターン 2 はターン 1 がバッファーに留まるかどうかに依存します。
- [`test.py`](src/test.py): ツールのディスパッチ、最終テキスト、およびマルチターン状態をオフラインでチェックします。

セクション 2 から 11 では、この `src/` が引き継がれ、`loop.py` が進化し、セクションごとに 1 つのファイルが追加されます。

```bash
python sections/01-agent-loop/src/test.py         # offline checks, no key
uv run python sections/01-agent-loop/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code): `QueryEngine.ts`、`query/`、`Tool.ts`。
- [mini-swe-agent ソース](https://github.com/swe-agent/mini-swe-agent): `agents/default.py`、`exceptions.py`、`environments/local.py`。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `docs/architecture.md`、`docs/agent-lifecycle.md`、`docs/subsystems/core.md`、`packages/core/agent-loop/src/agent.ts`、`packages/core/agent/src/types.ts`。
- [learn-claude-code · s01 Agent Loop](https://github.com/shareAI-lab/learn-claude-code): セクション フレーム。
