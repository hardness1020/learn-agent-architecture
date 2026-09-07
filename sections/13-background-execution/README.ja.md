# 13 · Background execution

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> メインループからゆっくりとした作業を開始し、後で報告します。

インストール、ビルド、テスト スイート、メモリ統合、または独自のループを実行する subagent など、一部の操作には時間がかかります。

基本的な agent loop は、ツール呼び出しが完了するのを待ってから、モデルを再度呼び出します。

高速に読むにはこれで十分です。エージェントが他のことをしている間に実行される可能性のある作業が遅いのは無駄です。

Background execution は次のことを行う必要があります。

1. ブロックせずに実行できる操作を決定します。
2. それらを開始し、すぐにハンドルを返します。
3. 実行中、完了、失敗、強制終了の状態を追跡します。
4. 後で完了メッセージをループに送り返します。

この層がないと、1 つの遅いコマンドによってエージェント全体がフリーズする可能性があります。

---

## メカニズム

![機構図](assets/13-background-execution.png)

次の 3 つの部分があります。

1. ハンドルを返すオフループ スターター。
2. タスクの状態を追跡する runtime。
3. 後のターンで完了通知を挿入するキュー。

ループは遅い作業を待ちません。

- バックグラウンド処理は実行オプションであり、特殊なツール タイプではありません。
- バックグラウンド呼び出しは、通常の `tool_result` をすぐに返します。
- ・実際の完了は後日別途お知らせいたします。
- subagent 全体をバックグラウンドで実行できます。

### 新機能: オフループ開始と通知ドレイン

`start` はワーカー スレッドで作業を実行し、タスク ID を返します。

```python
def start(self, fn):                                   # src/background.py; returns immediately
    self._next += 1
    tid = self._next
    self._state[tid] = "running"
    def work():
        try:
            self._finish(tid, "completed", str(fn()))  # enqueues a <task_notification>
        except Exception as e:
            self._finish(tid, "failed", f"{type(e).__name__}: {e}")
    threading.Thread(target=work, daemon=True).start()
    return tid
```

`drain_into` は、完了した通知を次のユーザーターンに折り込みます。

```python
def drain_into(messages, runtime):                     # src/background.py
    notes = runtime.drain() if runtime else []
    if notes and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = "\n".join(notes) + "\n\n" + messages[-1]["content"]
```

`backgroundable` は任意のツールをラップし、そのスキーマに `run_in_background` を追加します。

```python
def backgroundable(tool, runtime):                     # src/background.py; wraps ANY tool
    def run(a):
        if a.get("run_in_background"):
            inner = {k: v for k, v in a.items() if k != "run_in_background"}
            tid = runtime.start(lambda: tool.run(inner))
            return f"started background task {tid} ({tool.name}); ..."
        return tool.run(a)
    ...
    return replace(tool, run=run, ...)
```

ラッパーは、モデルが返す内容も設定します。バックグラウンド通話は作業を開始するだけです。タスク ID を返し、結果は独自のイベントとして後で到着します。
遅いツールにそのように名前を付けて説明します (`export` ではなく、`initiate_export`)。次に、モデルは、即時の `tool_result` を、応答としてではなく領収書として読み取ります。

### 統合方法

ループはターンの開始時に保留中の完了を排出します。

```python
background.drain_into(messages, runtime)               # src/loop.py
```

1 つのツールを呼び出して 1 つのツールの結果を得るというルールは今でも維持されています。完了の遅延は、古い `tool_use_id` の遅延 `tool_result` ではありません。新しい通知メッセージです。

### さらに読む

これは `src/` にはありません。これは ai-agent-book からのものであり、表内のシステムについては確認されていません。

**割り込みと安全ポイント** 一部の入力は、実行中のツール呼び出しが完了するまで待つことができません。
ユーザーの修正、キャンセル、またはアラートは通話中に発生する可能性があります。 1 つの答えは、すべての受信入力を 1 つのストリーム上のイベントにすることです。
ループは、安全なポイント、つまり、完了した tool result と次のモデル呼び出しの間のギャップでのみ、そのストリームを読み取ります。
通話の途中で書き込みを行うとトランスクリプトが壊れてしまうため、イベントはギャップが生じるまで待機します。

イベントの緊急度によって、どのギャップを待機するかが決まります。

- **キュー。** 次のギャップを待ちます。これは、完了および優先度の低い通知のデフォルトです。
- **キャンセル。** 実行中の通話を停止してギャップを開きます。修正により実行中の作業が無意味になる場合に使用します。
- **並列。** イベントをサイド ループで実行し、メイン ループはそのままにしておきます。

仕分けイベント自体は安価です。小規模なモデルで実行できるため、トリアージにはイベントごとに 1 回の呼び出しがかかります。

**プレースホルダーを中断します。** トランスクリプトが再度有効になるまで、キャンセルにはもう 1 つの手順が必要です。
停止された呼び出しにより、`tool_result` のない `tool_use` ブロックが残されたため、次のモデル呼び出しではそのペアを閉じる必要があります。
ai-agent-book はすぐに閉じます。通話が中断されたことを示すプレースホルダー `tool_result` を同じ ID に書き込みます。
これは上記の再利用禁止ルールに違反するものではありません。プレースホルダーによってペアが閉じられます。実際の結果は、独自の通知として後で届きます。
プレースホルダーは本の著者自身のデザインです。他の情報源ではそれについて説明されていません。

---

## システムごと

各エージェントがどのように作業をループ外に移動し、完了を報告するか。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **長所** |スループットが向上し、アイドル待機が解消されます。単純な待機では何もブロックされません。 | 1 つのレジストリは、シェル、ターミナル、および子エージェントに同様にサービスを提供します。 |
| **短所** |結果が遅く到着したり、順序が狂ったりする可能性があります。 runtime は、状態とクリーンアップを追跡します。 |アイドル状態のエージェントを起こすにはターンを消費するため、予算が必要です。 |
| **理由** | 1 つの遅いコマンドでエージェント全体がフリーズしてはなりません。 |完了したジョブは、モデルのポーリングを行わずにモデルに到達する必要があります。 |
| **方法: オフループ プリミティブ** |バックグラウンドのシェルとエージェントのタスク。サブプロセスが実行され、出力がリダイレクトされます。 |どのツールもバックグラウンドで実行フラグを受け取り、ジョブ ID を返します。 |
| **方法: 通知** | 1 つの共有キューを介した `<task_notification>` メッセージ。 |ジョブごとに 1 つの通知。先着者が勝ちとなり、重複は抑制されます。 |
| **方法: 再エントリー** |キューは、`now`、`next`、および `later` の優先度で、ターンの間に排出されます。 |忙しいエージェントは次のステップに進みます。アイドル状態の人はキャップ内で目覚めます。 |

---

## 障害モード

- **対話型プロンプトが停止します。** バックグラウンド コマンドが入力を待機します。プロンプトのような出力を検出し、非対話的に強制終了または再実行するようにモデルに通知します。
- **完了の喪失** 完了したタスクはループに到達しません。 1 つの共有キューを通じて完了を送信し、タスクに通知済みのマークを付けます。
- **通知のペアが間違っています。** 古い `tool_use_id` を再利用すると、トランスクリプトが壊れます。スタンドアロンの通知テキストを使用します。
- **キル後の副作用** タイムアウトまたはキャンセルでは、呼び出しが成功したかどうかはわかりません。ブラインド再試行では 2 回請求できます。最初に状態をクエリするか、べき等キーを送信します。
- **バッチイベントにより注意力が薄れます。** 1 つのドレインで複数の通知を 1 回にまとめることができます。その後、モデルは最後のものだけを答えます。イベントに番号を付け、概要行を追加します。
- **同時実行性が多すぎます。** 多くのバックグラウンド タスクがリソースを使い果たす可能性があります。キルパスと制限を追加します。
- **終了時にプロセス リークが発生します。** バックグラウンド作業がセッションを超えて存続する可能性があります。登録のクリーンアップ。

---

## 実行可能

[`src/`](src/) は 12 を繰り上げて次を追加します。

- [`background.py`](src/background.py): runtime、通知キュー、`drain_into`、および `backgroundable`。
- [`loop.py`](src/loop.py): モデル呼び出しの前に保留中の通知を排出します。
- [`test.py`](src/test.py): 開始、障害、ドレイン、およびバックグラウンド subagents をチェックします。
- [`demo.py`](src/demo.py): subagent をバックグラウンドで起動し、後でその結果を読み取ります。

```bash
python sections/13-background-execution/src/test.py         # offline checks, no key
uv run python sections/13-background-execution/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code タスク ソース](https://github.com/yasasbanukaofficial/claude-code): `tasks/LocalShellTask/`、`tasks/DreamTask/`。
- [Claude Code ツールとキュー ソース](https://github.com/yasasbanukaofficial/claude-code):
  `tools/BashTool/BashTool.tsx`、`tools/SleepTool/prompt.ts`、`utils/task/framework.ts`、`utils/messageQueueManager.ts`。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/jobs/jobs/src/index.ts`、`packages/jobs/jobs-local/src/index.ts`、`packages/jobs/tool-jobs/README.md`、
  `docs/subsystems/jobs.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s13_background_tasks](https://github.com/shareAI-lab/learn-claude-code): セクションのフレーム化。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`、中国語オリジナルの正規版。
  冪等性とキャンセルのセマンティクス、開始と完了の名前付け、安全なポイントでのイベントのトリアージ、割り込みプレースホルダー、バッチ化されたイベントのアテンション。
