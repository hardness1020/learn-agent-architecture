# 11 · Error recovery

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 失敗を分類してから、再試行、調整、停止のどれかを選びます。

agent の実行は多数のモデル呼び出しにまたがることがあります。どの呼び出しも、ネットワークの問題、過負荷、レート制限、出力の上限、context の溢れで失敗しえます。

モデル呼び出しは失敗の元の 1 つにすぎません。本番のコーディング agent を調べたある研究は、失敗を 4 つの層に分類しています。

- **API。** タイムアウト、レート制限、過負荷。
- **tool。** 非ゼロで終了したコマンド、または例外を投げたハンドラ。
- **context。** prompt の溢れ、または API が受け付けないメッセージ履歴。
- **制御フロー。** 繰り返すだけで前に進まないステップ。

まず層を見極めて、それから試行回数を数え始めます。先に数えると、どんな再試行でも直せないエラーに予算を使ってしまいます。

loop には、失敗の種類ごとに異なる応答が必要です。

1. 一時的なエラーは再試行します。
2. prompt や出力の上限が問題なら、調整してから再試行します。
3. 回復できないエラーなら停止します。

回復の仕組みがないと、一時的な API の失敗 1 回で長いタスクが終わってしまいます。

---

## 仕組み

![Mechanism diagram](assets/11-error-recovery.png)

モデル呼び出しを再試行のヘルパーで包みます。ヘルパーは失敗を分類し、範囲を区切った行動を取ります。

- 一時的なステータスコードは、バックオフしてから再試行します。
- prompt の溢れは compaction の callback を 1 回走らせてから再試行します。
- 過負荷が続くと、フォールバックのモデルに切り替えることがあります。
- 未知のエラーや再試行できないエラーは、そのまま投げます。

### 新規: 分類、バックオフ、再試行のヘルパー

```python
RETRY_STATUS = {408, 409, 429}                         # src/recovery.py; these plus any 5xx

def should_retry(status) -> bool:
    return status in RETRY_STATUS or (status is not None and 500 <= status < 600)

def retry_delay(attempt, retry_after=None) -> float:   # exponential backoff + jitter
    if retry_after is not None:
        return float(retry_after)
    base = min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)
    return base + base * 0.25 * random()
```

溢れの判定は、一般的なステータスの処理より先に行います。compaction で prompt を縮められるなら、`prompt_too_long` のエラーは回復可能になりえます。

```python
def _status(e):
    return getattr(e, "status_code", None)

def _is_overflow(e) -> bool:
    return getattr(e, "overflow", False) or "prompt is too long" in str(e).lower()
```

`with_retry` は試行ごとの状態を保持します。

```python
def with_retry(call, on_overflow=None, fallback_model=None,
               max_retries=DEFAULT_MAX_RETRIES, sleep=time.sleep):
    consecutive_529 = 0
    overflowed = False
    for attempt in range(1, max_retries + 2):
        try:
            return call()
        except Exception as e:
            if _is_overflow(e):
                if on_overflow is None or overflowed:
                    raise
                overflowed = True
                on_overflow()
                continue
            status = _status(e)
            if status is None:
                raise
            if status == 529:
                consecutive_529 += 1
                if fallback_model and consecutive_529 >= MAX_529_RETRIES:
                    raise FallbackTriggered(fallback_model)
            if attempt > max_retries or not should_retry(status):
                raise
            sleep(retry_delay(attempt, getattr(e, "retry_after", None)))
```

### 統合のしかた

loop は自分のモデル呼び出しを包みます。

```python
response = recovery.with_retry(
    lambda: model(messages, registry, system),
    on_overflow=lambda: _reactive_trim(messages),
    fallback_model=fallback_model)
```

- 回復が包むのはモデル呼び出しだけです。
- `_reactive_trim` は溢れ 1 回分の再試行のために `messages[]` をその場で書き換えます。
- 回復をあきらめたときは、エラーを隠さずに表に出します。

### 参考

以下は `src/` にはありません。ai-agent-book に基づく内容で、表にあるシステムで確認が取れているわけではありません。

**例外を投げない loop を捕まえる。** agent がテストファイルを実行し、同じエラーを読み、また同じテストファイルを実行する、という状況を考えます。
何も投げないので再試行の経路は発火せず、どの上限にも達しません。これは制御フローの失敗であり、専用の検出器が要ります。

検出器はフィンガープリントです。tool の名前とその引数を組み合わせたものです。同じフィンガープリントが繰り返されていれば、agent が同じ呼び出しをやり直しています。
ステップ数の上限でも実行は終わりますが、終わるのは予算を使い切ってからです。フィンガープリントのカウンタなら数ステップで終わらせられますし、どの呼び出しで詰まっているかも名指しできます。

回復の経路にも、それぞれ専用のカウンタが要ります。経路ごとに失敗を数えれば、失敗が続く経路は全体の上限を待たずに自分のブレーカーを落とせます。

**静かになった stream を止める。** stream は接続して、token をいくつか送り、そこで止まることがあります。
そのときには接続のタイムアウトは既に過ぎているので、何も発火せず loop は待ち続けます。

対処は 2 本目のタイマーです。接続のタイムアウトと並べてアイドルの watchdog を置き、一定の時間内に token が来なければ呼び出しをキャンセルします。
すると再試行のヘルパーは、そのキャンセルをふつうの一時的な失敗として扱います。

**壊れたメッセージ履歴を修復する。** turn の途中でクラッシュすると、対応する `tool_result` のない `tool_use` ブロックが残ることがあります。
次のリクエストは作業の内容ではなくメッセージの形で失敗し、対が直るまで失敗し続けます。

修復が何を意味するかは、その transcript が何のためのものかで決まります。答えは 2 通りです。

- **製品の harness は修復します。** 呼び出しが中断されたと書いたプレースホルダの結果を追加し、実行を続けます。
- **学習データの harness は修復を拒みます。** でっち上げた結果は、実際には走らなかったステップを教えてしまいます。

**呼び出し側に失敗をどこまで見せるか。** 回復は 1 つの決断ではありません。失敗をどれくらい見せるべきかで段階を付けます。

1. **静かに再試行する。** 呼び出し側には最終結果しか見えません。
2. **劣化させて続ける。** 小さめの結果を返し、何が欠けているかを伝えます。
3. **失敗を表に出す。** 試行の一覧を出し、モデルが別の道を試せるようにします。

最初の 2 段階のエラーは隔離が要ります。ヘルパーの中に留め、回復をあきらめたときだけ外に出します。
早くモデルに届いたエラーは最終的なものに見えるので、モデルが既に成功していた作業をやり直す可能性があります。

**回復が自分自身を呼び込むのを止める。** エラーの経路が hook、要約、通知を起動することがあります。
その処理がまたモデルを呼び、また失敗し、その新しい失敗がまた同じ経路を起動します。

この連鎖は 2 つのルールで切れます。エラーの経路では副作用のあるロジックを止めることと、それでも残るものには再帰の深さのカウンタを持つことです。
バックグラウンドの呼び出しには再試行を一切与えません。クリティカルパスの外にあるので、再試行はメインの loop が必要とする quota を使うだけです。

**上限の値はどこから来るか。** この section のどの上限も、誰かが選んだ数字です。再試行は何回か、何回連続で失敗させるか、アイドルの窓をどれだけ取るか。
どれも直感ではなく、計測した失敗から選びます。本書の 3 回で compaction を打ち切る上限は、回復の失敗が繰り返された本番のデータから来ています。

---

## システム別

回復はモデル呼び出しを包みます。loop の本体は変わりません。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | 経路ごとの対処は、一律の再試行より多くの実行を救う。 | 範囲を区切った 3 つの経路。クラッシュしても完全な trajectory が残る。 | 再試行が記録されるので、再開した session がそれを把握できる。 |
| **欠点** | 維持すべき分岐と上限が増える。 | 救える実行は少ない。溢れは中断で終わり、3 回の形式エラーでも中断する。 | フォールバックのモデルがない。always モードは永久に再試行する。 |
| **理由** | 一時的な API の失敗 1 回で長いタスクを終わらせるべきではない。 | 再試行し、形式エラーは返し、終了理由を名指しする。 | ログが真実なので、回復は新しい turn を再生する。 |
| **方法: retry** | 429、408、409、5xx でバックオフ。`retry-after` が優先。 | tenacity のバックオフ、4 秒から 60 秒、10 回まで。 | 失敗した turn の後にエラーのイベント、それから新しい turn。 |
| **方法: token handling** | 出力の上限を上げる、`max_tokens` での停止の後に続ける、または compaction する。 | なし。溢れると実行を中断する。 | 溢れのコードは 1 つ。刈り込んでから要約する。 |
| **方法: model fallback** | 過負荷 (529) が続いたらフォールバックのモデルへ。 | なし。 | なし。再試行の turn が同じリクエストを組み直す。 |

---

## 失敗モード

- **再試行の嵐。** 多数のクライアントが過負荷で再試行すると、負荷はさらに悪化する。再試行を制限し、`retry-after` に従う。
- **回復が無限に続く。** エスカレーション、継続、compaction は loop になりうる。経路ごとに上限を設ける。
- **溢れを縮められない。** 反応的な compaction が 1 回失敗したら、永久に compaction を続けずに停止する。
- **エラーが消える。** 握りつぶしたエラーは、結果の欠けた transcript を残す。回復を使い切ったら失敗を表に出す。
- **stop hook が API のエラーを繰り返す。** API エラーのメッセージでは stop hook を飛ばす。
- **エラーなしで詰まる。** 繰り返されるだけの呼び出しは何も投げないので、再試行の経路は発火しない。tool と引数のフィンガープリントの繰り返しを数え、実行を止める。
- **stream が静かに停止する。** stream は開いてから静かになりうる。接続のタイムアウトは既に過ぎているので、何も発火しない。アイドルの watchdog を足す。
- **修復が記録を汚す。** プレースホルダの `tool_result` は製品の実行を生かし続ける。同時に、実際には走らなかったステップを記録する。学習データとして残す transcript は修復しない。
- **途中のエラーが漏れる。** 回復が終わる前に見せたエラーは最終的なものに見え、モデルが作業をやり直す。回復をあきらめるまでヘルパーの中に留める。

---

## 実行

[`src/`](src/) は 10 を引き継ぎ、次を追加します。

- [`recovery.py`](src/recovery.py): 再試行の分類、バックオフ、溢れの処理、フォールバックの起動。
- [`loop.py`](src/loop.py): 自分のモデル呼び出しを `with_retry` で包みます。
- [`test.py`](src/test.py): 不安定な呼び出しの偽物で各経路を動かします。
- [`demo.py`](src/demo.py): live な実行の中に過負荷を 1 回だけ模擬して差し込みます。

```bash
python sections/11-error-recovery/src/test.py         # offline checks, no key
uv run python sections/11-error-recovery/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `services/api/withRetry.ts`, `query.ts`, `services/api/claude.ts`, `services/api/errors.ts`, `query/tokenBudget.ts`, `utils/context.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `models/utils/retry.py`, `models/litellm_model.py`, `run()` and `max_consecutive_format_errors` in `agents/default.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/llm/llm-retry/README.md`, `packages/llm/llm-retry/src/types.ts`, `packages/core/agent-loop/src/agent.ts`,
  `docs/subsystems/llm-streaming.md`, `docs/subsystems/core.md`, `docs/subsystems/persistence.md`.
- [ai-agent-book · chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》, 李博杰; 中国語の原著が正典):
  4 層の失敗の分類、tool と引数による loop のフィンガープリント、アイドルの watchdog、`tool_result` の対の修復とその製品対学習データの分岐、
  エラーの隔離を伴う段階的な回復、死のスパイラルへの防御。脚注 ch5-3 は、この分類が本番の agent (Claude Code を含む) を調べた研究に基づくことを示し、
  実装の変化が速いことを注意しています。また、3 回で compaction を打ち切る上限を、計測した本番の失敗から定めています。
- [learn-claude-code · s11_error_recovery](https://github.com/shareAI-lab/learn-claude-code): section の枠組み。
