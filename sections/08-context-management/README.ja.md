# 8 · Context management

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 長い session を context の上限内に保ちます。

`messages[]` は実行中に伸びます。tool result、assistant の返答、ユーザーの turn が、それぞれテキストを足していきます。長い session はいずれモデルの context の上限に達します。

context management は session を使える状態に保ちます。次のモデル呼び出しの前に、古い内容を削除、スタブ化、退避、または要約します。

context が埋まると、次のことが起きます。

1. API がリクエストを拒否することがあります。
2. 呼び出しが遅くなり、費用も増えます。
3. 古くて役に立たない内容が、いま必要なタスクの情報と競合します。

3 つめには名前があります。context rot です。無関係なテキストが積み上がるほど、モデルは正しい事実を見つけにくくなります。
これは window が埋まるずっと前から始まります。agent は動き続けます。ただ、判断が悪くなります。

つまり compaction は、収まるかどうかと費用だけの話ではありません。文脈内学習は、推論よりも検索に近い働きをします。
モデルは、書かれている事実なら見つけられます。何十もの turn に散らばった事実を組み合わせるのは苦手です。
結論を一度書き留めるほうが、毎回の呼び出しでモデルに導き直させるより安上がりです。
だから良い要約は、window にまだ余裕があるときでも答えを良くします。

このレイヤがないと、prompt が収まらなくなった時点で長いタスクは失敗します。

---

## 仕組み

![Mechanism diagram](assets/08-context-management.png)

要約の前に、安価な削減手段を使います。安価な削減手段は局所的で、ほとんど情報を失いません。要約はモデル呼び出しの費用がかかり、細部を失うことがあります。

Claude Code は層になった順序を使います。

```text
budget   -> persist huge tool results to disk, leave a preview
snip     -> drop stale middle turns, keep head + recent tail
micro    -> replace old tool-result bodies with a stub
collapse -> optional independent context system
auto     -> LLM summarizes the whole history into one message
--- on prompt_too_long despite the above ---
reactive -> truncate the head and re-summarize, with a retry cap
```

順序が重要です。たとえば大きな tool result は、どの pass がその本体をスタブに置き換えるより先に、退避しておくべきです。

### 本節の追加: 削減の pass

```python
def manage(messages, summarizer=None):                 # src/context.py, run every turn
    _budget(messages)                                  # persist huge results   (lossless)
    _micro(messages, KEEP_RECENT)                      # stub old result bodies (cheap)
    if summarizer and estimate_tokens(messages) > TOKEN_LIMIT:
        return _auto(messages, KEEP_RECENT, summarizer)  # summarize history (lossy, last resort)
    return messages
```

- `manage` は安価な pass を毎 turn 走らせます。
- `_budget` は大きすぎる tool result をディスクに書き、短いプレビューを残します。
- `_micro` は古い tool result の本体をスタブにします。
- `_auto` は最初の turn と直近の末尾を残し、中間を要約します。
- `summarizer=None` にすると、デモでは情報を失う要約が無効になります。

### 既存の構成への組み込み

context management は、モデル呼び出しのたびにその前で走ります。

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)   # 8 · keep context under the window
    response = model(messages, registry)
    ...
```

このセクションは loop の本体そのものを変えます。これまでのセクションは tool や dispatch の振る舞いを足すだけで、loop には触れませんでした。
context の削減はモデル呼び出しのたびにその前で走らなければならないので、loop の中に置く必要があります。

loop の不変条件は変わりません。妥当な `messages[]` でモデルを呼び、返答と tool result を追加します。

### 対照: 書き出される tool 出力

Claude Code とこのセクションの `_budget` は、どちらも大きな tool result をその場で縮めます。切られたテキストは失われます。

deepseek-harness は起きたことを編集しません。session ログには追記しかせず、モデルが見る messages はそのログの投影です。
削減はログに載るもう 1 つのイベントで、どの範囲を置き換えるかを示します。だから再開や fork をしても同じ見え方が再現されます。

大きな tool の出力は、それ以前の別経路を通ります。インラインのバイト上限を超えた結果は、tool が返った時点で spill store に入ります。
store は全文を保存し、参照先を返します。context に残るのは先頭と末尾のプレビュー、その参照先、そして読むか grep せよという案内です。
つまり出力にはまだ手が届きます。残りが必要になったとき、モデルはそのファイルを求めます。

[`src/spill.py`](src/spill.py) はこれを削ぎ落としたものです。対照のためのデモであり、`manage()` には接続していません。だから後のセクションは同じ pass をそのまま引き継ぎます。

### さらに読む

ここに書くことは `src/` にはありません。ai-agent-book に基づくもので、表にあるシステムで確認が取れているわけではありません。

**スタブは毎回同じ文字列でなければならない。** tool result を置き換えるテキストは prefix の一部なので、バイト単位で同一でなければなりません。
最初に置き換えたときに決めて、それを使い回します。session をディスクから復元した後も同じです。
新しいタイムスタンプや新しいパスで描き直されるスタブは prefix を変え、それ以降のキャッシュを失わせます。

**圧縮とキャッシュは正反対を望む。** compaction は履歴を書き換えます。キャッシュは履歴に手を触れないときにこそ効きます。
編集はその地点から先のキャッシュを無効にするので、次の呼び出しは prefix 全体を読み直します。
毎 turn 少しずつ削れば、その作り直しも毎 turn 起きます。token のしきい値で大きめの削減を 1 回行えば、作り直しは 1 回で済みます。
どちらにせよ compaction は API 呼び出しの間に走り、呼び出しの内側では走りません。

**この pass はサーバー側でも走らせられる。** Claude API の context editing は古い tool result を prefix から落とすので、harness 側のコードは要りません。
それでもキャッシュは一度作り直されます。だからこれは毎 turn ではなく、順序の中では溢れる側の端に位置します。

**要約は session 全体ではなく、いまのタスクのために書く。** 起きたこと全部の再掲は、次の呼び出しが必要とするものではありません。
代わりに問いを 1 つ立てます。次の呼び出しがまだ必要としているものは何か。優先度の高い順に、次を残します。

- すでに決まったアーキテクチャと設計の判断。
- 作成または変更したファイルと、その変更内容。
- 直近の検査やテストの合否。
- 未解決の TODO と現在の手順。

何かを落とさなければならないとき、最初に落とすのは加工前の tool の出力です。budget の pass が大きな結果をすでにディスクへ書いているので、必要になれば agent が読み戻せます。

---

## システム別

各 agent がどう場所を空けると判断し、何を取り除くか。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | 長い session が生き残る。削減は安価で、出力も読み直せる。 | 予定を組むことも調整することもない。監査しやすい。 | 履歴が壊されることはない。 |
| **欠点** | pass に順序のルールが要る。要約は細部を落とすことがある。 | 履歴は増える一方。長い実行は溢れて死ぬ。 | ログがディスク上で増え、ロックと畳み込みが要る。 |
| **理由** | 対話的な session は終わりが決まらないので、window は埋まる。 | 予算が先に実行を終わらせる前提 (セクション 21)。 | ログが真実なので、縮むのは見え方だけ。 |
| **方法: 起動条件** | token のしきい値と、`prompt_too_long` での予備の経路。 | 観測のたび、描画の時点で。 | 各手順で測る圧力と、確認された溢れ。 |
| **方法: 戦略** | 安価な削減が先 (退避、スタブ)、要約は最後。 | 長い出力を先頭と末尾に切り詰める。compaction はなし。 | spill、削除、その後に要約のイベント。 |
| **方法: 予算** | 出力と安全のための余裕を確保する。 | 観測 1 件につき 1 万文字。 | 振り分けたモデルごとの比率。0.8 で compaction、0.16 を残す。 |

---

## 失敗モード

- **要約が必要な細部を落とす。** 出力の全文を退避し、必要なときにファイルを読み直す。
- **compaction が繰り返し失敗する。** リトライの上限かサーキットブレーカーを使う。
- **1 つの巨大な turn がそれでも溢れる。** `prompt_too_long` に反応し、範囲を区切った最後の手段の切り詰めを行う。
- **pass の順序が誤ってデータを失う。** 古い結果をスタブ化する前に、大きな結果を退避する。
- **tool の対応が壊れる。** `tool_use` と対応する `tool_result` を切り離さない。
- **スタブのテキストがぶれる。** 新しいタイムスタンプやパスで描き直されるプレビューは prefix を変え、キャッシュを失わせる。最初に使った文字列を固定する。
- **毎 turn 切り詰める。** 編集はその地点から先のキャッシュを無効にするので、小さな削減を何度も行うと 1 回のまとめた pass より高くつく。しきい値で起動する。
- **モデルが要約を信じる。** 注入された状態は事実として読まれ、再確認されることはめったにない。退避した原本への参照を残し、誤った要約を捕まえられるようにする。

---

## 実行

[`src/`](src/) は 07 を引き継ぎ、次を追加します。

- [`context.py`](src/context.py): `budget`、`micro`、`auto` の pass を `manage` から走らせる。
- [`loop.py`](src/loop.py): 毎 turn の先頭で `context.manage()` を呼ぶ。
- [`spill.py`](src/spill.py): deepseek-harness との対照。大きすぎる結果を丸ごと保存し、context にはプレビューとパスを残す。
- [`test.py`](src/test.py): 各 pass を単独で検査し、加えて全文が読める spill を検査。
- [`demo.py`](src/demo.py): context management を組み込んだ loop を動かす。

```bash
python sections/08-context-management/src/test.py         # offline checks, no key
uv run python sections/08-context-management/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `services/compact/autoCompact.ts`, `microCompact.ts`, `timeBasedMCConfig.ts`, `compact.ts`, `utils/toolResultStorage.ts`, `query.ts`, `query/tokenBudget.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent): the observation template in `config/mini.yaml`, `abort_exceptions` in `models/litellm_model.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/compaction/compaction/src/index.ts`, `packages/compaction/compaction-basic/README.md`, `packages/llm/token-meter/src/index.ts`,
  `packages/spill/spill/src/index.ts`, `packages/spill/spill-policy/README.md`, `docs/subsystems/compaction.md`, `docs/subsystems/session.md`.
- [learn-claude-code · s08_context_compact](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book · chapter 2](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  context rot, in-context learning as retrieval, the compression and cache interplay, task-aware compression with retention priorities,
  API-level context editing, the frozen tool-result stub, and the finding that models read an injected summary as fact.
- [Lost in the Middle](https://arxiv.org/abs/2307.03172) (Liu et al., TACL 2024): retrieval accuracy drops for facts placed in the middle of a long context. Grounds context rot.

推測を含みます。上記の Claude Code のソースリポジトリには完全には現れていません。

- `snipCompact.ts`: `snipCompactIfNeeded(messages)` の呼び出し箇所だけが見えています。
- `reactiveCompact.ts`: reactive の経路は `compact.ts` にあるようです。
