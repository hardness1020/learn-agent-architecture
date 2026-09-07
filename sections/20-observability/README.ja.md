# 20 · Observability & evaluation

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 見えないものは直せません。そして、誰も記録していない実行は採点できません。

agent は無人で動き、副作用を起こし、お金を使います。モデルの呼び出しはブラックボックスです。token を消費し、現実の操作を引き起こします。

計装がなければ、基本的な問いにすら答えられません。何をしたのか。tool はどのくらいの頻度で失敗したのか。この session はいくらかかったのか。

このセクションが担当するのは記録です。各ステップが何をして、いくらかかったかを書き出し、その記録を保存できるだけきれいに保ちます。

変更で品質が上がったか下がったかは別の仕事です。セクション 23 がその仕事を担当し、このセクションが記録したものの上で動きます。

記録を省くと、コストの急増は毎回不意打ちになります。バグ報告はどれも再現できません。eval セットには、元にできる実データがありません。

---

## 仕組み

![Mechanism diagram](assets/20-observability.png)

loop の制御フローには決して触れない、独立した 2 本のパイプラインです。

テレメトリは inline で動きます。各ステップが投げっぱなしの logger を呼び出します。

イベントは sink、つまりターミナル、ファイル、Datadog のようなバックエンドといった出力先へ送られます。
logger は sink が接続されるまでイベントをキューに溜め、その後サンプリングし、機微なフィールドを取り除き、fan out します。

評価は専用のタスクセットに対してオフラインで動きます (セクション 23)。そのタスクセットを組み立てる材料が、このセクションの記録です。

- `emit` は決してブロックせず、決して例外を送出しません。ですから logging の障害が loop を止めたりクラッシュさせたりすることはありません (セクション 1)。
- イベントは sink が接続されるまでキューに溜まり、その後排出されます。ですからテレメトリの準備前でも loop はログを出せます。
- サンプリングはレートでイベントを捨てます。スクラブは allowlist にあるフィールドだけを残します。ですからコードやパスが漏れることはありません。
- コストはモデルごとに積み上がり、1 つの USD 合計になります。実行中と終了時に表示されます。

### 本節の新規: fire-and-forget のイベント記録

`telemetry.py` はイベントを emit します。イベントは sink が接続されるまでキューに溜まり、その後サンプリング、スクラブ、fan out されます。`emit` は決して例外を送出しません。

```python
def emit(self, name, **meta):                          # src/telemetry.py
    if not self.sinks:
        self._queue.append((name, meta))               # buffer until a sink is ready
        return
    self._deliver(name, meta)

def _deliver(self, name, meta):
    if not self.sample(name):                          # dropped by sampling rate
        return
    clean = scrub(meta)                                # allowlist before any backend sees it
    for sink in self.sinks:
        try:
            sink(name, clean)
        except Exception:                              # one bad sink never breaks the loop
            pass
```

- sink が 1 つも接続されていない間、イベントは `_queue` に溜まります。`attach` は同じ `_deliver` 経路でそれらを排出するので、キューに溜まったイベントもサンプリングとスクラブを通ります。
- `scrub` は `SAFE_FIELDS` だけを残します。ですから安全と分かっていない値 (コード、ファイルパス、prompt) がバックエンドに届くことはありません。
- 例外を投げる sink は握りつぶされます。ですから壊れたバックエンドが 1 つあっても loop を止めたりクラッシュさせたりできません。

### 本節の新規: モデルごとのコストとオフライン eval

コストはモデルごとに積み上がり、1 つの USD 累計になります。

```python
def add(self, model, input_tokens, output_tokens):    # src/telemetry.py
    i, o = self.by_model.get(model, (0, 0))
    self.by_model[model] = (i + input_tokens, o + output_tokens)
    pi, po = PRICES.get(model, (0.0, 0.0))             # modelCost.ts pricing tiers
    self.cost_usd += input_tokens * pi + output_tokens * po
    return self.cost_usd
```

- `add` は token 単価を引いて、支出を `cost_usd` に積み上げます。これが実行中と終了時に表示される数字です。
- この合計がカバーするのは session です。どのタスクがお金を使ったかは決して分かりません。

ここでの `run_eval` は、考えうる最小の eval です。固定のタスクセットを候補ビルドに対して再生し、合格数を数え、レートを返します。
セクション 23 は同じエントリポイントの下に、環境、シミュレートされた user、繰り返し実行を置きます。そのレートの小さな下落がたいていノイズである理由も説明します。

### 既存の構成への組み込み方

demo はモデルの wrapper にテレメトリを載せています。loop は変わりません。

```python
def model(messages, registry, system):
    r = client.messages.create(...)
    cost.add(MODEL, r.usage.input_tokens, r.usage.output_tokens)   # cost rollup
    tel.emit("model_call", model=MODEL, tokens=..., cost_usd=...)  # scrubbed event
    return r
run_turn([...goal...], lambda m, r, s: model(m, r, SYSTEM), reg, Session(mode=DEFAULT))   # the one agent call
```

- テレメトリは外から観測します。wrapper がイベントを emit してコストを追跡するので、`run_turn` と dispatch はセクション 13 とバイト単位で同一のままです。
- sink が各イベントを print し、session のコストが最後に print され、その後オフラインの `run_eval` が固定のタスクセットを採点します。
- 上流はすべて変わりません。observability は横から見る観測者であって、loop の新しいステップではありません。

### さらに読む

以下はどれも `src/` にはありません。ai-agent-book と 2 つのトレース標準に由来するもので、表にあるシステムで確認が取れているわけではありません。

**フラットなイベントではなく span。** span は 1 回の実行の中の 1 単位の仕事です。モデル呼び出し、tool 呼び出し、検索などです。trace は実行全体です。
どの span も次を記録します。

- いつ始まり、どれだけ時間がかかったか、
- 成功したかどうか、
- どの span が親か、
- その仕事を説明する自由形式の属性。

重要なのは親へのリンクです。これが実行中の span を木構造にするので、木を上から読めば、どのステップが失敗し、
どのステップが遅く、各枝がいくらかかったかが分かります。

フラットなイベントではそれができません。イベントは呼び出しが起きたことは言いますが、その呼び出しがどのステップに属していたかは言いません。
1 つの user リクエストが、多数のモデル呼び出し、tool 呼び出し、検索に化けます。入れ子になっているものもあれば、同時に走っているものもあります。
それをタイムスタンプで解きほぐすのは当て推量です。

2 つの標準が span の形を固定するので、バックエンド側で推測する必要がなくなります。

- OpenTelemetry は span そのものを定義します。trace id、親 id、時刻情報、ステータス、属性です。
- OpenInference はその上に載る LLM の仕事に名前を付けます。prompt、completion、モデル、token 数、tool call です。

これらの名前に対して計装を一度書いておけば、バックエンドの切り替えは書き直しではなく設定変更で済みます。

エクスポートも `emit` と同じルールに従います。ホットパスから外します。span はキューに入り、バックグラウンドの worker がバッチで送るので、
collector が遅くても実行には何のコストもかかりません。このセクションの `emit` は、これら全部のフラット版です。
同じイベントに trace id と親 id を足せば、木構造はそこにあります。

**非線形なコストとタスクごとの上限。** コストはモデルが読む token 数に連動し、どの turn も会話全体を再送します。
ですから turn 2 で返ってきた tool result は、turn 3、4、5 でもう一度支払われます。
context に足したものは、その後のあらゆる turn で支払われ、合計は turn 数より速く増えます。
ステップ数だけでは予測できません。

harness の 2 つの機能が請求の一部を削りますが、その節約分は単純に足し算できません。

- Prompt caching (セクション 10) は同じままだった prefix を割り引きます。
- Compaction (セクション 8) は古い turn を context から落とします。

両者は重なります。compaction は、caching なら割り引かれていたはずの token を消してしまいます。

session 単位の合計はこれをすべて隠します。どのタスクがお金を使ったかを決して言わないからです。
ですからタスクごとにコストを追い、タスクごとに上限を与えます。この上限は、終わらない loop をステップ上限が止めるのと同じやり方で実行を止めます (セクション 1)。
ここでの出典はこの本だけで、外部の裏付けを引いていません。ですからこのコストモデルは、一人の著者の現場報告として読んでください。

**trace が eval セットを育てる。** 2 本のパイプラインは一方向に交わります。本番の trace が eval のタスクになります。3 つのステップで一方をもう一方に変えます。

- **選ぶ。** 学ぶ価値のある実行を残します。エラーになったもの、user がやり直したり訂正したりしたもの、他よりはるかに高くついたものです。
  問題なく終わった実行は何ももたらしません。
- **スクラブ。** コードとパスをバックエンドから締め出す allowlist が、タスクファイルからも同じように締め出します。
- **再構成。** trace は開始状態とすべての tool call を保持しているので、タスクのセットアップと、その実行が到達すべきだった結果の両方を供給できます。

これを継続的にやれば、eval セットは user が実際にやることに追随します。セクション 23 は、そこに入ってきたものを採点します。

---

## システム別

各 agent がどうテレメトリを emit し、支出を追い、eval セットを育てるか。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | 本番の可視性が高く、安価で安全。 | クラッシュした実行でもファイルが残る。 | 追加で計装するものがない。モデルが見たものがそのままログ。 |
| **欠点** | 何が起きたかは言うが、答えが良かったかは言わない。 | 本番テレメトリがほぼない。 | マスキング規則を同梱しない。配送は欠落や重複を起こしうる。 |
| **理由** | 本番は loop に触れずに監視する必要がある。 | benchmark はオフラインで採点するので、記録の完全さが効く。 | session のログが既に記録なので、それを出力する。 |
| **方法: telemetry** | イベントは sink 待ちでキューに入り、その後サンプリングとスクラブ。 | 実行ごとに trajectory ファイル 1 つ、毎ステップ保存。 | session イベントをマスキング処理を通して外部へ複製。 |
| **方法: cost tracking** | モデルごとの token を価格付けして session 合計へ。 | 呼び出しごとの価格が実行合計と全体合計に積み上がる。 | 再生パスがログをドルではなく token で価格付けする。 |
| **方法: eval feed** | ソースにはない。スクラブ済み trace が回帰ケースになる。 | 保存された trajectory が benchmark ランナーに供給される。 | 記録された実行を key なしで再生し、fixture として使う。 |

---

## 失敗モード

- **テレメトリがホットパスに乗る。** ブロックしたり例外を投げたりする logging 呼び出しは loop を止めます (セクション 1)。ネットワーク待ちをする span exporter も同じです。
  緩和策: 投げっぱなしにし、sink 前のキュー、sink ごとの killswitch、バックグラウンド worker からのバッチ送信を用意します。
- **機微データがログに漏れる。** コード、ファイルパス、prompt が広くアクセスできるバックエンドや、trace から作ったタスクファイルに届きます。
  緩和策: ログ出力可能なフィールドを allowlist にし、fan out や保存の前に残りをスクラブします。
- **親リンクのないフラットなストリーム。** どのモデル呼び出しがどのステップに属していたかを言うものがなく、失敗した実行はタイムスタンプで継ぎ合わせるしかありません。
  緩和策: すべてのイベントに trace id と親 span id を付け、バックエンドが既に理解している命名規約に従います。
- **コストのずれに気付かない。** モデルの入れ替えや暴走した loop が支出を何倍にもし、session 単位の合計は、それを燃やした 1 つのタスクを隠します。
  緩和策: モデルごととタスクごとの合計を実行中と終了時に表示し、タスクごとの上限と loop のステップ上限を置きます (セクション 1)。
- **eval セットが本番からずれる。** オフラインのタスクが実際の使われ方を取りこぼし、スイートは通るのに user は失敗します (セクション 23)。
  緩和策: 失敗した実行と高くついた実行のスクラブ済み trace を、絞り込んでタスクセットに入れ続けます。

---

## 実行

[`src/`](src/) は 19 を引き継ぎ、次を追加します。

- [`telemetry.py`](src/telemetry.py): イベント logger (`Telemetry.emit`、キューと排出、`sample`、`scrub`)、モデルごとの `CostTracker`、オフラインの `run_eval`。
- [`test.py`](src/test.py): キュー後の排出、サンプリング、実際の tool dispatch を通したスクラブと sink の隔離、モデルごとのコスト、退化したビルドを捕まえる eval。
- [`demo.py`](src/demo.py): モデルの wrapper に載せたテレメトリで観測する agent の 1 turn、実行中の session コスト、その後のオフライン eval。

loop と dispatch は変わりません。テレメトリは外から観測し、それが育てる eval はホットパスの外で動きます (セクション 23)。

```bash
python sections/20-observability/src/test.py         # offline checks, no key
uv run python sections/20-observability/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code analytics](https://github.com/yasasbanukaofficial/claude-code):
  `services/analytics/index.ts` (queue + `logEvent`)、`sink.ts`、`datadog.ts`、`firstPartyEventLogger.ts`、`sinkKillswitch.ts`、`shouldSampleEvent`。
- [Claude Code cost and diagnostics](https://github.com/yasasbanukaofficial/claude-code):
  `cost-tracker.ts`、`utils/modelCost.ts`、`costHook.ts` (`formatTotalCost`)、`diagnosticTracking.ts`、`upstreamproxy/relay.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) の `dsh-v0.1.0-rc.7`:
  `docs/subsystems/telemetry.md`、`docs/subsystems/token-meter.md`、`packages/llm/llm-replay/README.md`、`docs/subsystems/invariants.md`。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter6.md`、中国語原文が正典。
  span の木、タスクごとの上限を伴う非線形な agent コスト、本番 trace を eval セットへ再利用する話。
  コスト分析はそこで外部の引用を持たないので、単一ソースです。
- [OpenTelemetry tracing](https://opentelemetry.io/docs/specs/otel/trace/api/): span そのもの、親リンク、時刻情報、ステータス、属性。
- [OpenInference](https://github.com/Arize-ai/openinference): span 上の LLM と tool の属性に名前を付ける semantic conventions。
- 評価は Claude Code のソースには存在せず、ここではセクション 23 が担当します。ホールドアウトのタスクセットと LLM-as-judge は再構成と一般的な実践のままです。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `agents/default.py` の `serialize` と `save`、`models/__init__.py` の `GLOBAL_MODEL_STATS`、`run/benchmarks/swebench.py`、`run/utilities/inspector.py`。
- 枠組み: [learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code)。
