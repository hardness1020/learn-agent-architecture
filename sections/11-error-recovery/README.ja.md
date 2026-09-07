# 11 · Error recovery

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 失敗を分類し、再試行、調整、または停止します。

エージェントの実行は、多くのモデル呼び出しにまたがる場合があります。ネットワークの問題、過負荷、レート制限、出力制限、またはコンテキスト オーバーフローが原因で、呼び出しが失敗する可能性があります。

モデル呼び出しは失敗の原因の 1 つにすぎません。プロダクションコーディングエージェントに関するある研究では、障害を次の 4 つの層に分類しています。

- **API.** タイムアウト、レート制限、および過負荷。
- **ツール。** ゼロ以外で終了するコマンド、または発生するハンドラー。
- **コンテキスト** プロンプト オーバーフロー、または API が拒否するメッセージ履歴。
- **制御フロー。** 繰り返してもどこにも到達しないステップ。

まずレイヤーを作成してから、試行のカウントを開始します。最初にカウントすると、再試行では修正できないエラーにバジェットが費やされます。

ループには、さまざまな障害に対してさまざまな応答が必要です。

1. 一時的なエラーを再試行します。
2. プロンプトまたは出力制限に問題がある場合は、調整して再試行します。
3. エラーが回復できない場合は停止します。

回復しないと、一時的な API 障害が 1 回発生すると、長いタスクが終了する可能性があります。

---

## メカニズム

![機構図](assets/11-error-recovery.png)

モデル呼び出しを再試行ヘルパーでラップします。ヘルパーは障害を分類し、制限されたアクションを実行します。

- 一時的なステータス コードをバックオフして再試行します。
- プロンプト オーバーフローは圧縮コールバックを 1 回実行し、その後再試行します。
- 過負荷が繰り返されると、フォールバック モデルがトリガーされる可能性があります。
- 不明なエラーまたは再試行不可能なエラーが発生します。

### 新機能: 分類、バックオフ、および再試行ヘルパー

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

オーバーフローは一般的なステータス処理の前にチェックされます。圧縮によってプロンプトを縮小できる場合、`prompt_too_long` エラーは回復可能です。

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

### 統合方法

ループはモデル呼び出しをラップします。

```python
response = recovery.with_retry(
    lambda: model(messages, registry, system),
    on_overflow=lambda: _reactive_trim(messages),
    fallback_model=fallback_model)
```

- リカバリはモデル呼び出しのみをラップします。
- `_reactive_trim` は、1 回のオーバーフロー再試行で `messages[]` を変更します。
- リカバリが諦めると、エラーは隠れるのではなく表面化します。

### さらに読む

これは `src/` にはありません。これは ai-agent-book からのものであり、表内のシステムについては確認されていません。

**決して発生しないループをキャッチします。** エージェントがテスト ファイルを実行し、同じエラーを読み取り、同じテスト ファイルを再度実行するとします。
何もスローされないため、再試行パスは起動せず、境界に到達しません。これは制御フローの障害であり、独自の検出器が必要です。

検出器はフィンガープリント、つまりツール名とその引数です。繰り返されるフィンガープリントは、エージェントが同じ通話をやり直していることを示します。
ステップ キャップによって実行は終了しますが、それは予算がすべてなくなった場合に限られます。指紋カウンターは数ステップで通話を終了し、保留中の通話に名前を付けることができます。

回復パスにも独自のカウンターが必要です。パスごとに障害をカウントするため、障害が発生し続けるパスは、グローバル キャップを待つのではなく、独自のブレーカーをトリップします。

**静かになったストリームを強制終了します。** ストリームは接続し、いくつかのトークンを送信してから停止することができます。
その時点で接続タイムアウトがすでに経過しているため、何も起動せず、ループは待機します。

修正は 2 番目のタイマーです。接続タイムアウトの横にアイドル ウォッチドッグを追加し、ウィンドウ内にトークンが到着しない場合は呼び出しをキャンセルします。
その後、再試行ヘルパーはキャンセルを通常の一時的な失敗として処理します。

**壊れたメッセージ履歴を修復します。** ターン途中でクラッシュすると、一致する `tool_result` のない `tool_use` ブロックが残る可能性があります。
次のリクエストは、作業ではなくメッセージの形状で失敗し、ペアが修正されるまで失敗し続けます。

修復の意味はトランスクリプトの目的によって異なります。答えは 2 つあります。

- **製品 harness が修復されます。** 通話が中断されたことを示すプレースホルダー結果が追加され、実行は続行されます。
- **トレーニング データ harness は拒否されます。** でっち上げられた結果は、決して実行されなかったステップを教えることになります。

**呼び出し元がどの程度の障害を認識する必要があるか。** 回復は 1 つの決定ではありません。失敗がどの程度目立つかによって等級付けします。

1. **静かに再試行してください。** 呼び出し元には最終結果のみが表示されます。
2. **デグレードして続行します。** より小さい結果を返し、何が欠けているかを示します。
3. **失敗を表面化します。** モデルが別のパスを試行できるように、試行をリストします。

最初の 2 つのグレードのエラーは隔離が必要です。それらをヘルパーの中に保持し、回復があきらめた場合にのみ解放します。
モデルに早期に到達したエラーは最終的なものであるように見え、モデルはすでに成功した作業をやり直す可能性があります。

**フィード自体からの回復を停止します。** エラー パスにより、hook、概要、または通知がトリガーされる可能性があります。
その作業ではモデルが再度呼び出され、再び失敗し、新しい失敗によって同じパスが再びトリガーされます。

2 つのルールが連鎖を断ち切ります。エラー パスの副作用ロジックをオフにし、生き残ったものに対して再帰深度カウンターを保持します。
バックグラウンド呼び出しでは再試行はまったく行われません。バックグラウンド呼び出しはクリティカル パスから外れているため、再試行はメイン ループに必要なクォータのみを消費します。

**境界の由来** このセクションのすべての境界は、誰かが選んだ数値です。つまり、再試行回数、ストライク回数、アイドル ウィンドウの長さです。
直感ではなく、測定された失敗に基づいてそれぞれの失敗を選択します。この本の 3 ストライク圧縮限界は、繰り返しのリカバリ失敗に関する運用データに基づいています。

---

## システムごと

リカバリはモデル呼び出しをラップします。ループ本体は変わりません。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **長所** |特定のパスを使用すると、一括再試行よりも多くの実行を節約できます。 | 3 つの境界のあるパス。クラッシュすると完全な軌道が残ります。 |再試行はログに記録されるため、再開されたセッションは再試行を認識します。 |
| **短所** |維持する必要がある分岐と境界がさらに増えます。 |実行回数を減らします。オーバーフローは中止され、3 つのフォーマット エラーも中止されます。 |フォールバックモデルはありません。常にモードでは永久に再試行されます。 |
| **理由** | 1 回の一時的な API 障害によって、長いタスクが終了することはありません。 |再試行し、フォーマット エラーを元に戻し、出口に名前を付けます。 |ログは真実なので、回復は新しいターンをリプレイします。 |
| **方法: 再試行** | 429、408、409、5xx のバックオフ。 `retry-after` が勝ちます。 |粘り強さのバックオフ、4 ～ 60 秒、10 回の試行。 |失敗したターンの後にエラー イベントが発生し、その後新たなエラー イベントが発生します。 |
| **方法: トークンの処理** |出力キャップを上げるか、`max_tokens` 停止後に続行するか、コンパクトにします。 |なし。オーバーフローにより実行が中止されます。 | 1 つのオーバーフロー コード: プルーニングしてから要約します。 |
| **方法: モデルのフォールバック** |繰り返しの過負荷後のフォールバック モデル (529)。 |なし。 |なし。再試行ターンでは同じリクエストが再構築されます。 |

---

## 障害モード

- **再試行の嵐。** 多くのクライアントが過負荷を再試行すると、負荷が悪化する可能性があります。再試行を制限し、`retry-after` を尊重します。
- **無限のリカバリ。** エスカレーション、継続、圧縮がループする可能性があります。各パスをバインドします。
- **オーバーフローは縮小できません。** 1 つのリアクティブ圧縮が失敗した場合は、永久に圧縮するのではなく停止します。
- **エラーが消えます。** エラーが飲み込まれると、結果が欠落したトランスクリプトが残ります。回復後の表面障害は使い果たされます。
- **Stop hook は API エラーを繰り返します。** API エラー メッセージの場合は Stop hooks をスキップします。
- **エラーなしでスタックします。** 呼び出しを繰り返しても何も発生しないため、再試行パスは起動されません。繰り返されるツールと引数のフィンガープリントをカウントし、実行を停止します。
- **サイレントストリームストール** ストリームが開いてから静かになることがあります。接続タイムアウトがすでに経過しているため、何も起動されません。アイドル状態のウォッチドッグを追加します。
- **修復により記録が汚染されます。** プレースホルダー `tool_result` により、製品の実行が維持されます。実行されなかったステップも記録されます。トレーニング データとして保存されているトランスクリプトを修復しないでください。
- **中間エラーのリーク。** 回復が完了する前に表示されるエラーは最終的なものであるように見え、モデルは作業をやり直します。回復が諦めるまでヘルパーの中に入れておいてください。

---

## 実行可能

[`src/`](src/) は 10 を繰り上げて次を追加します。

- [`recovery.py`](src/recovery.py): 再試行分類、バックオフ、オーバーフロー処理、およびフォールバック トリガー。
- [`loop.py`](src/loop.py): モデル呼び出しを `with_retry` でラップします。
- [`test.py`](src/test.py): 偽の不安定な呼び出しで各パスを駆動します。
- [`demo.py`](src/demo.py): ライブ実行に 1 つのシミュレートされたオーバーロードを挿入します。

```bash
python sections/11-error-recovery/src/test.py         # offline checks, no key
uv run python sections/11-error-recovery/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code ソース](https://github.com/yasasbanukaofficial/claude-code):
  `services/api/withRetry.ts`、`query.ts`、`services/api/claude.ts`、`services/api/errors.ts`、`query/tokenBudget.ts`、`utils/context.ts`。
- [mini-swe-agent ソース](https://github.com/swe-agent/mini-swe-agent):
  `agents/default.py` の `models/utils/retry.py`、`models/litellm_model.py`、`run()`、および `max_consecutive_format_errors`。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/llm/llm-retry/README.md`、`packages/llm/llm-retry/src/types.ts`、`packages/core/agent-loop/src/agent.ts`、
  `docs/subsystems/llm-streaming.md`、`docs/subsystems/core.md`、`docs/subsystems/persistence.md`。
- [ai-agent-book · Chapter 5](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md) (《深入理解 AI Agent》、李博杰、中国語の原文は正規版です):
  4 層の障害分類法、ツールと引数のループ フィンガープリント、アイドル ウォッチドッグ、製品とトレーニング データの分割による `tool_result` ペアの修復、
  エラー隔離による段階的回復とデススパイラル防御。その脚注 ch5-3 は、生産エージェントの研究からの分類法をソースとしています。
  その中には Claude Code が含まれており、実装が急速に進んでいることを警告しています。また、測定された実稼働エラーに基づいて 3 回の圧縮限界を設定します。
- [learn-claude-code · s11_error_recovery](https://github.com/shareAI-lab/learn-claude-code): セクションのフレーム化。
