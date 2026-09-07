# 13 · Background execution

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 遅い作業をメインの loop の外で始め、後から結果を報告します。

時間のかかる操作があります。インストール、ビルド、テストスイート、memory の統合、あるいは自分の loop を回す subagent です。

基本的な agent の loop は、tool の呼び出しが終わるのを待ってから次のモデル呼び出しを行います。

速い読み取りならそれで問題ありません。agent が別のことをしている間に走れる遅い作業には無駄です。

background execution は次を満たす必要があります。

1. どの操作ならブロックせずに走れるかを判断すること。
2. それらを開始し、すぐにハンドルを返すこと。
3. 実行中、完了、失敗、停止済みの状態を追うこと。
4. 完了のメッセージを後から loop に送り返すこと。

この層がないと、遅いコマンド 1 つで agent 全体が固まります。

---

## 仕組み

![Mechanism diagram](assets/13-background-execution.png)

部品は 3 つです。

1. loop の外で作業を始め、ハンドルを返す starter。
2. task の状態を追う runtime。
3. 後の turn で完了の通知を差し込むキュー。

loop は遅い作業を待ちません。

- バックグラウンド化は実行時のオプションであって、特別な tool の種類ではありません。
- バックグラウンド化した呼び出しは、すぐにふつうの `tool_result` を返します。
- 本当の完了は、後から別の通知として届きます。
- subagent 全体をバックグラウンドで走らせることもできます。

### 新規: loop 外での開始と通知の取り出し

`start` は worker のスレッドで作業を走らせ、task の id を返します。

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

`drain_into` は完了の通知を次の user turn に畳み込みます。

```python
def drain_into(messages, runtime):                     # src/background.py
    notes = runtime.drain() if runtime else []
    if notes and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = "\n".join(notes) + "\n\n" + messages[-1]["content"]
```

`backgroundable` は任意の tool を包み、そのスキーマに `run_in_background` を足します。

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

このラッパーは、モデルが何を受け取るかも決めます。バックグラウンド化した呼び出しは作業を始めるだけです。task の id を返し、結果は後から独立したイベントとして届きます。
遅い tool は、その意味が伝わる名前と説明にします (`export` ではなく `initiate_export`)。そうすればモデルは、すぐ返る `tool_result` を答えではなく受領証として読みます。

### 統合のしかた

loop は turn の最初に、溜まっている完了を取り出します。

```python
background.drain_into(messages, runtime)               # src/loop.py
```

tool の呼び出し 1 つに tool の結果 1 つ、というルールは変わりません。遅れて来る完了は、古い `tool_use_id` に対する遅延した `tool_result` ではありません。新しい通知のメッセージです。

### 参考

以下は `src/` にはありません。ai-agent-book に基づく内容で、表にあるシステムで確認が取れているわけではありません。

**割り込みと安全な地点。** 入力の中には、走っている tool の呼び出しの終了を待てないものがあります。
ユーザーの訂正、キャンセル、アラートは、呼び出しの途中で届くことがあります。1 つの答えは、入ってくる入力をすべて 1 本のストリーム上のイベントにすることです。
loop がそのストリームを読むのは安全な地点、つまり tool の結果が出てから次のモデル呼び出しまでの隙間だけです。
呼び出しの途中に書き込むと transcript が壊れるので、イベントはその隙間を待ちます。

イベントの緊急度が、どの隙間まで待つかを決めます。

- **キューに入れる。** 次の隙間まで待ちます。完了や優先度の低い通知の既定はこれです。
- **キャンセルする。** 走っている呼び出しを止めて、今すぐ隙間を作ります。訂正によって走っている作業が無意味になったときに使います。
- **並行に走らせる。** そのイベントを脇の loop で処理し、メインの loop はそのままにします。

イベントの仕分け自体は安いです。小さいモデルでもできるので、トリアージのコストはイベントごとに 1 回の呼び出しです。

**割り込み時のプレースホルダ。** キャンセルの後、transcript が再び正当になるにはもう 1 ステップ要ります。
止めた呼び出しは `tool_result` のない `tool_use` ブロックを残しており、次のモデル呼び出しにはその対が閉じている必要があります。
ai-agent-book はすぐに閉じます。同じ id に対して、呼び出しが中断されたと書いたプレースホルダの `tool_result` を書き込みます。
これは上の再利用禁止のルールを破りません。プレースホルダは今この場で対を閉じるだけです。本当の結果は後から独立した通知として届きます。
このプレースホルダは本書の著者自身の設計です。他の出典にこれを述べたものはありません。

---

## システム別

各 agent が作業をどう loop の外に出し、完了をどう報告するか。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **利点** | スループットが上がり、待ち時間がなくなる。単なる待機は何もブロックしない。 | 1 つの registry が shell、ターミナル、child agent を等しく扱う。 |
| **欠点** | 結果が遅れて、順序も入れ替わって届きうる。runtime が状態と後始末を追う。 | アイドルの agent を起こすと turn を消費するので、予算が要る。 |
| **理由** | 遅いコマンド 1 つで agent 全体を固めてはならない。 | 終わったジョブは、モデルがポーリングしなくてもモデルに届かなければならない。 |
| **方法: off-loop primitive** | バックグラウンドの shell と agent の task。サブプロセスは走り続け、出力はリダイレクトされる。 | どの tool もバックグラウンド実行のフラグを取り、ジョブの id を返す。 |
| **方法: notification** | 共有のキュー 1 本を通る `<task_notification>` のメッセージ。 | ジョブごとに通知 1 つ。最初の完了が勝ち、重複は抑制される。 |
| **方法: re-entry** | キューは turn のあいだに、`now`、`next`、`later` の優先度で取り出される。 | 忙しい agent は次のステップで受け取る。アイドルの agent は上限の範囲で起こされる。 |

---

## 失敗モード

- **対話的な入力待ちで止まる。** バックグラウンドのコマンドが入力を待つ。入力待ちらしい出力を検出し、停止するか非対話で再実行するようモデルに知らせる。
- **完了が失われる。** 終わった task が loop に届かない。完了は共有のキュー 1 本を通して送り、task に通知済みの印を付ける。
- **通知の対応付けが誤る。** 古い `tool_use_id` を再利用すると transcript が壊れる。独立した通知のテキストを使う。
- **停止後の副作用。** タイムアウトやキャンセルでは、呼び出しが届いたかどうか分からない。何も考えずに再試行すると二重に請求されうる。先に状態を問い合わせるか、冪等キーを送る。
- **まとめられたイベントが注意を薄める。** 1 回の取り出しで複数の通知が 1 つの turn に畳み込まれうる。するとモデルは最後の 1 つにしか答えない。イベントに番号を振り、要約の行を足す。
- **並行度が高すぎる。** バックグラウンドの task が多いとリソースを使い切りうる。停止の経路と上限を足す。
- **終了時のプロセスの漏れ。** バックグラウンドの作業が session より長生きしうる。後始末を登録する。

---

## 実行

[`src/`](src/) は 12 を引き継ぎ、次を追加します。

- [`background.py`](src/background.py): runtime、通知のキュー、`drain_into`、`backgroundable`。
- [`loop.py`](src/loop.py): モデル呼び出しの前に、溜まっている通知を取り出します。
- [`test.py`](src/test.py): 開始、失敗、取り出し、バックグラウンドの subagent を確認します。
- [`demo.py`](src/demo.py): subagent をバックグラウンドで起動し、その結果を後から読みます。

```bash
python sections/13-background-execution/src/test.py         # offline checks, no key
uv run python sections/13-background-execution/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code task sources](https://github.com/yasasbanukaofficial/claude-code): `tasks/LocalShellTask/`, `tasks/DreamTask/`.
- [Claude Code tool and queue sources](https://github.com/yasasbanukaofficial/claude-code):
  `tools/BashTool/BashTool.tsx`, `tools/SleepTool/prompt.ts`, `utils/task/framework.ts`, `utils/messageQueueManager.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/jobs/jobs/src/index.ts`, `packages/jobs/jobs-local/src/index.ts`, `packages/jobs/tool-jobs/README.md`,
  `docs/subsystems/jobs.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s13_background_tasks](https://github.com/shareAI-lab/learn-claude-code): section の枠組み。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, 中国語の原著が正典。
  冪等性とキャンセルの意味論、開始と完了を分ける命名、安全な地点でのイベントのトリアージ、割り込み時のプレースホルダ、まとめられたイベントと注意。
