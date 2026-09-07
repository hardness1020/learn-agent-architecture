# 8 · Context management

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 長時間のセッションはコンテキスト制限内に保ちます。

`messages[]` は実行中に増加します。 tool result、アシスタントの応答、およびユーザーの順番ごとにテキストが追加されます。セッションが長いと、最終的にはモデルのコンテキスト制限に達します。

Context management はセッションを使用可能な状態に保ちます。次のモデル呼び出しの前に、古いコンテンツを削除、スタブ、永続化、または要約します。

コンテキストが満たされると、次のようになります。

1. API はリクエストを拒否できます。
2. 通話が遅くなり、通話料が高くなります。
3. 古くてあまり役に立たないコンテンツが現在のタスク情報と競合します。

3 番目の項目には、context rot という名前が付いています。無関係なテキストが積み重なると、モデルが適切な事実を見つける頻度が低くなります。
これは、ウィンドウがいっぱいになるかなり前に開始されます。エージェントは実行を続けます。それはより悪い決定をするだけです。

したがって、圧縮は適合性とコストだけを重視するものではありません。コンテキスト内学習は、推論よりも検索に似ています。
モデルは、書き留められた事実を見つけることができます。何十ターンにもわたって広がる事実を組み合わせるのは苦手です。
結論を一度書き留めるほうが、呼び出しごとにモデルに再度結論を導出させるよりもコストがかかりません。
したがって、要約が適切であれば、ウィンドウにまだ余裕がある場合でも、回答が向上します。

このレイヤーがないと、プロンプトが適合しなくなると、長いタスクは失敗します。

---

## メカニズム

![機構図](assets/08-context-management.png)

要約する前に安価なリデューサーを使用してください。安価なリデューサーはローカルであり、ほとんどロスレスです。要約にはモデル呼び出しがかかり、詳細が失われる可能性があります。

Claude Code は階層化された順序を使用します。

```text
budget   -> persist huge tool results to disk, leave a preview
snip     -> drop stale middle turns, keep head + recent tail
micro    -> replace old tool-result bodies with a stub
collapse -> optional independent context system
auto     -> LLM summarizes the whole history into one message
--- on prompt_too_long despite the above ---
reactive -> truncate the head and re-summarize, with a retry cap
```

順序が重要です。たとえば、大きな tool result は、パスが本体をスタブに置き換える前に永続化する必要があります。

### 新機能: 削減パス

```python
def manage(messages, summarizer=None):                 # src/context.py, run every turn
    _budget(messages)                                  # persist huge results   (lossless)
    _micro(messages, KEEP_RECENT)                      # stub old result bodies (cheap)
    if summarizer and estimate_tokens(messages) > TOKEN_LIMIT:
        return _auto(messages, KEEP_RECENT, summarizer)  # summarize history (lossy, last resort)
    return messages
```

- `manage` は毎ターン安いパスを実行します。
- `_budget` は、サイズが大きいツールの結果をディスクに書き込み、短いプレビューを残します。
- `_micro` は古いツール結果ボディをスタブします。
- `_auto` は最初のターンと最近の末尾を保持し、その後中間を要約します。
- `summarizer=None` は、デモで非可逆要約を無効にします。

### 統合方法

Context management は、各モデル呼び出しの前に実行されます。

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)   # 8 · keep context under the window
    response = model(messages, registry)
    ...
```

このセクションではループ本体自体を変更します。前のセクションでは、ツールまたはディスパッチ動作を追加し、ループはそのままにしておきました。
コンテキスト削減はすべてのモデル呼び出しの前に実行する必要があるため、ループ内で実行する必要があります。

ループは依然として同じ不変条件を維持します。つまり、有効な `messages[]` を使用してモデルを呼び出し、応答とツールの結果を追加します。

### コントラスト: こぼれたツール出力

Claude Code とこのセクションの `_budget` は両方とも、所定の場所にある巨大な tool result を縮小します。切り取られたテキストは消えます。

deepseek-harness は何が起こったのか決して編集しません。セッション ログは追加されるだけであり、モデルに表示されるメッセージはそのログの投影です。
リダクションは、どのスパンを置き換えるかを示すもう 1 つのログ イベントであるため、再開またはフォークされたセッションでは同じビューが再生されます。

大きなツールの出力は、その前に別のパスをたどります。インライン バイト キャップを超えた結果は、ツールが戻った瞬間にスピル ストアに送られます。
ストアは全文を保存し、ロケーターを返します。コンテキスト内に残るのは、先頭と末尾のプレビュー、そのロケーター、およびそれを読み取るか grep するためのヒントです。
したがって、出力にはまだ到達可能です。モデルは、残りが必要なときにファイルを要求します。

[`src/spill.py`](src/spill.py) はこれを取り除いたものです。これはコントラスト デモであり、`manage()` に接続されていないため、後のセクションでは同じパスが引き継がれます。

### さらに読む

これは `src/` にはありません。これは ai-agent-book からのものであり、表内のシステムについては確認されていません。

**スタブは毎回同じ文字列である必要があります。** tool result を置き換えるテキストはプレフィックスの一部であるため、バイト同一である必要があります。
最初の交換時に選択し、セッションがディスクから復元された後も含めて再利用します。
新しいタイムスタンプまたは新しいパスを使用して再レンダリングされるスタブは、プレフィックスを変更し、プレフィックスがなくなった後のキャッシュを変更します。

**圧縮とキャッシュは逆のことを望んでいます。** 圧縮は履歴を書き換えます。キャッシュは、履歴を放置した場合にのみ効果を発揮します。
編集するたびに編集ポイント以降のキャッシュが無効になるため、次の呼び出しでプレフィックス全体が再読み取りされます。
毎ターン少しずつトリミングすると、その再構築が毎ターン行われます。トークンのしきい値でさらに大きな削減を 1 回実行すると、それが 1 回発生します。
いずれの場合も、圧縮は API 呼び出し間で実行され、呼び出し内では実行されません。

**API はサーバー上でこのパスを実行できます。** Claude API でのコンテキスト編集はプレフィックスから古いツールの結果を削除するため、harness にはそのためのコードが付属しません。
それでもキャッシュは一度再構築されます。これにより、毎ターンではなく、オーダーのオーバーフローエンド近くになります。

**セッション全体ではなく、現在のタスクの概要を書きます。** 起こったことすべての要約は、次の通話で必要なものではありません。
代わりに 1 つの質問をしてください。次の呼び出しには何が必要ですか?これらを最も優先度の高いものから順に保持してください。

- アーキテクチャと設計に関する決定はすでに行われています。
- 作成または変更されたファイル、およびその中で何が変更されたか。
- 最後のチェックまたはテストの合格および不合格のステータス。
- TODO と現在のステップを開きます。

何かを実行する必要がある場合は、生のツールの出力が最初に実行されます。予算パスではすでに大きな結果がディスクに書き込まれているため、エージェントは重要なときに結果を読み戻すことができます。

---

## システムごと

各エージェントがスペースを空けることをどのように決定し、何を削除するかを決定します。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **長所** |長いセッションは存続します。削減は低コストで、出力は再読み取り可能です。 |スケジュールや調整をする必要はありません。監査が容易。 |歴史は決して破壊されることはありません。 |
| **短所** |パスには順序付けルールが必要です。概要では詳細が省略される場合があります。 |歴史は成長するばかりです。ロングランはオーバーフローで停止します。 |ログはディスク上で成長するため、ロックとフォールドが必要になります。 |
| **理由** |インタラクティブセッションは無制限であるため、ウィンドウはいっぱいになります。 |予算が最初に実行を終了すると仮定します (セクション 21)。 |ログは真実なので、視野だけが縮小します。 |
| **方法: トリガー** |トークンのしきい値と、`prompt_too_long` のフォールバック。 |レンダリング時のすべての観察。 |各ステップで圧力を測定し、オーバーフローを確認しました。 |
| **方法: 戦略** |最初に安価なリデューサー (永続、スタブ)、最後に概要を示します。 |長い出力を先頭と末尾に切り詰めます。圧縮はありません。 |流出、剪定、そして要約イベント。 |
| **方法: 予算** |出力バッファと安全バッファを予約します。 |観測ごとに 10,000 文字。 |配線モデルごとの比率: 0.8 でコンパクト、0.16 を維持。 |

---

## 障害モード

- **概要では必要な詳細が失われます。** 完全な出力を保持し、必要に応じてファイルを再読み込みします。
- **圧縮が繰り返し失敗します。** 再試行キャップまたはサーキット ブレーカーを使用してください。
- **とにかく 1 つの大きなターンがオーバーフローします。** `prompt_too_long` には、境界のある最後の手段のトリムで反応します。
- **パス順序が間違っているとデータが失われます。** 古い結果をスタブする前に、大きな結果を保持します。
- **壊れたツール ペア。** `tool_use` を、一致する `tool_result` から分割しないでください。
- **スタブ テキストがドリフトする。** 新しいタイムスタンプまたはパスで再レンダリングされるプレビューでは、プレフィックスが変更され、キャッシュが削除されます。初めて使用するときは文字列をフリーズしてください。
- **毎ターンのトリミング。** 編集ごとに編集ポイント以降のキャッシュが無効になるため、小さな削減が多くなると、1 回のバッチ パスよりも多くのコストがかかります。しきい値でトリガーします。
- **モデルは概要を信頼します。** 注入された状態は事実として読み取られ、再チェックされることはほとんどありません。間違った要約を検出できるように、永続化されたオリジナルへのポインタを残しておきます。

---

## 実行可能

[`src/`](src/) 07 を前方に繰り上げて次を追加します。

- [`context.py`](src/context.py): `budget`、`micro`、および `auto` パスは、`manage` を通過します。
- [`loop.py`](src/loop.py): 毎ターンの先頭で `context.manage()` を呼び出します。
- [`spill.py`](src/spill.py): deepseek-harness の対照: 特大の結果は全体として保存され、コンテキストはプレビューとパスを保持します。
- [`test.py`](src/test.py): 各パスを個別にチェックし、さらに全文を読み取り可能な状態に保つスピルをチェックします。
- [`demo.py`](src/demo.py): context management が接続されたループを駆動します。

```bash
python sections/08-context-management/src/test.py         # offline checks, no key
uv run python sections/08-context-management/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code):
  `services/compact/autoCompact.ts`、`microCompact.ts`、`timeBasedMCConfig.ts`、`compact.ts`、`utils/toolResultStorage.ts`、`query.ts`、 `query/tokenBudget.ts`。
- [mini-swe-agent ソース](https://github.com/swe-agent/mini-swe-agent): `config/mini.yaml`、`models/litellm_model.py` の `abort_exceptions` の観測テンプレート。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/compaction/compaction/src/index.ts`、`packages/compaction/compaction-basic/README.md`、`packages/llm/token-meter/src/index.ts`、
  `packages/spill/spill/src/index.ts`、`packages/spill/spill-policy/README.md`、`docs/subsystems/compaction.md`、`docs/subsystems/session.md`。
- [learn-claude-code · s08_context_compact](https://github.com/shareAI-lab/learn-claude-code): セクション フレーム。
- [ai-agent-book · Chapter 2](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》、李博杰、中国語の原文は正規版です):
  コンテキストの腐敗、取得としてのコンテキスト内学習、圧縮とキャッシュの相互作用、保持優先順位を備えたタスク認識圧縮、
  API レベルのコンテキスト編集、凍結されたツール結果のスタブ、およびモデルが挿入された概要を事実として読み取るという発見。
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) (Liu et al.、TACL 2024): 長いコンテキストの途中に配置されたファクトの検索精度が低下します。根拠コンテキスト腐ってます。

推定。上記の Claude Code ソース リポジトリには完全には存在しません。

- `snipCompact.ts`: `snipCompactIfNeeded(messages)` 呼び出しサイトのみが表示されます。
- `reactiveCompact.ts`: リアクティブ パスは `compact.ts` にあるようです。
