# 14 · Scheduling

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> ユーザーの入力だけでなく、時計からも agent の turn を始めます。

バックグラウンドの作業も、誰かか何かに始めてもらう必要があります。後で走らせたい、あるいは繰り返したいタスクは多くあります。レポート、リマインダー、ポーリングのタスクなどです。

scheduling は将来のトリガーを保存します。それが発火すると prompt をキューに入れます。ふつうの loop がその prompt を新しい turn として処理します。

scheduling は次を満たす必要があります。

1. schedule を 1 つの turn の外に保存すること。
2. loop とは独立に時刻を見張ること。
3. schedule が発火したら prompt をキューに入れること。
4. 必要なら schedule を再起動をまたいで永続化すること。

この層がないと、agent はユーザーの入力に反応することしかできません。

---

## 仕組み

![Mechanism diagram](assets/14-scheduling.png)

時計を loop から切り離します。scheduler は時刻を見張ります。モデルを直接呼ぶことはしません。

発火の時点で、scheduler は prompt をキューに入れるだけです。駆動側が turn のあいだ、走っている turn がないときにキューを取り出し、
ユーザーの入力を扱うのと同じ agent の loop に、各 prompt を通します。

- schedule はデータです。走らせる prompt、発火時刻、そして任意の繰り返し間隔です。scheduler はそれぞれを task として保存します。
- 一度きりのものは 1 回発火してから自分を消します。
- 繰り返す schedule は次の間隔に再セットされます。
- 永続的な schedule は再起動を生き延びますが、ホストが落ちている間は発火しません。
- heartbeat は、質問をする繰り返しの schedule です。起きて、情報源を確認し、たいていは言うことは何もないと判断します。

### 新規: scheduler と発火のキュー

`tick` は期限の来た task を確認します。発火とは prompt をキューに入れることです。

```python
def tick(self):                                       # src/scheduler.py; called by a daemon thread
    now = self._clock()
    for tid, t in list(self._tasks.items()):
        if now >= t["due"]:
            self._pending.put({"prompt": t["prompt"], "channel": t.get("channel")})
            if t["every"]:                            # enqueue, do not run the model here
                t["due"] = now + t["every"]
            else:
                self._tasks.pop(tid, None)
    self._save()                                      # durable tasks only
```

- 時計は差し替えられるので、テストでは偽の時計を使います。
- `run()` は daemon のスレッドで `tick` を呼びます。
- `_save` は永続的な task を JSON に保存します。
- 同じパスで新しい `Scheduler` を作ると、永続的な task を読み直し、id を引き継ぎます。

### 新規: 答えの届け方

発火した実行には待っている人間がいないので、答えには外に出る経路が必要です。task ごとに channel を指定できます。
channel は task 上のフィールドです。`create(..., channel="console")` がそれを保存し、`tick` が prompt と一緒にキューに入れます。
取り出される項目はもともと `{"prompt": ..., "channel": ...}` なので、駆動側は答えの行き先を調べる必要がありません。

`deliver` はその turn の答えを振り分けます (Hermes は cron の出力をそのジョブのチャットプラットフォームに届けます)。

```python
SILENT = "[SILENT]"                              # a fired run may decide nothing is worth sending

def deliver(channels, fired, text) -> bool:      # src/scheduler.py
    if not fired.get("channel") or text.lstrip().startswith(SILENT):
        return False
    channels[fired["channel"]](text)
    return True
```

- `channels` は channel の名前を送信の callable に対応付けます (ここでは print。本物のアダプタは section 19 の仕事です)。
  task が channel を指定し、駆動側が対応表を持ちます。どちらも相手の詳細を知りません。
- 答えが `[SILENT]` で始まるとき、`deliver` は channel への送信を飛ばします。これは、予定された確認が、ユーザーに伝えるほどのものを見つけなかったときの取り決めです
  (変化のなかったポーリングなど)。駆動側は全文を保持したままなので、ログに残せます。
- channel の指定がなければ答えはローカルに留まります。届け先を持ち込む前の挙動です。
- `bool` の戻り値があることで、駆動側は答えを黙って失う代わりに代替手段に回せます (デモは届かなかった答えを print します)。

### heartbeat

決してプッシュしてこない情報源もあります。webhook のないメールボックス、フィードのないページ、聞かれたときだけ答えるサービスなどです。
そうしたものに残された唯一のトリガーは時計です。そのパターンが heartbeat です。繰り返しの schedule で、その prompt は agent に、動けではなく見に行けと伝えます。
情報源を確認し、メッセージにする価値があるほど何かが変わったかを判断し、そうでなければ何も言いません。

heartbeat の実行が報告に値するものを見つけなければ、`[SILENT]` と答えます。上のルールにより、`deliver` は何も送りません。
その tick はモデル呼び出し 1 回分の費用がかかるだけでメッセージは出ないので、channel を溢れさせずに schedule を高い頻度で走らせられます。

heartbeat と cron のエントリは、ここでは同じ部品を使います。prompt、繰り返し間隔、channel です。違うのは prompt だけです。
cron の prompt は命令を出します。heartbeat の prompt は質問をします。

### 統合のしかた

scheduling は 2 つの半分からできています。`tick` は自分の daemon スレッドで走り (section 13 の background execution)、モデルには一切触れず、発火時にキューに入れるだけです。

```python
def run(self):                                        # src/scheduler.py; started by sched.run()
    def loop():
        while not self._stop.wait(self.CHECK_INTERVAL):   # wakes once per second
            self.tick()
    threading.Thread(target=loop, daemon=True).start()    # daemon: never keeps the process alive
```

turn そのものは前面で走ります。駆動側が turn のあいだにキューを取り出し、発火した task ごとに `run_turn` を 1 回呼びます。

```python
for task in sched.drain():                            # src/demo.py · between turns
    messages = [{"role": "user", "content": task["prompt"]}]
    deliver(channels, task, run_turn(messages, model, reg, session))
```

発火した prompt は、user 発の turn と同じ形の新しい turn になります。同じ loop、permission、hook、memory、context の管理、回復の経路を使います。その答えは task の channel に振り分けられます。

### 参考

以下は `src/` にはありません。ai-agent-book に基づく内容で、表にあるシステムで確認が取れているわけではありません。

**時計の限界。** heartbeat で効いてくる設定は 1 つ、間隔です。
これが請求額と最悪の遅延を同時に決めてしまい、この 2 つは互いに引っ張り合います。
間隔が短ければモデルは頻繁に起きて、たいていは何も見つけません。間隔が長ければ安く済みますが遅くなります。
どんな間隔にしてもこれは直りません。時計はイベントを見張るのではなく状態を標本抽出するので、最後に見た時刻は分かっても、物事が起きた時刻は分かりません。

**プッシュが使えるならそちらを選びます。** 情報源から agent を呼べるなら、トリガーはイベントの発生と同時に発火し、ポーリングの費用はゼロになります。
なので順番はこうです。情報源が対応していればプッシュ、対応していなければ heartbeat、そして月曜のレポートのように本当に時刻で決まる作業には cron です。
入ってくるプッシュ側は section 19 が扱います。

---

## システム別

各 agent が、予定された作業をいつ走らせるかをどう決めるか。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | 単純で、外に出ない。永続的な schedule は再起動を生き延びる。 | 人が見ていなくても発火し、ホスト型のサービスも要らない。 | リマインダーが session と一緒に再生される。取りこぼした発火は 1 つの turn にまとまる。 |
| **欠点** | tick が走るのは session が動いている間だけ。遠隔のトリガーにはサービスが要る。 | ゲートウェイと、二重発火を防ぐ lock が要る。 | 固定のレートのみ。cold な状態からは何も発火しない。 |
| **理由** | ローカルの session が動いている前提。 | ゲートウェイはサーバーなので、人が見ていなくても schedule は発火する。 | リマインダーは会話の状態なので、session のログがそれを持つ。 |
| **方法: trigger** | ticker 上の cron、sleep、遠隔のトリガー。 | ゲートウェイの tick 上の cron。ユーザーのタイムゾーンで。 | 一定の遅延の後、指定時刻、または最短で 5 分ごと。 |
| **方法: durability** | session の状態、または lock 付きの JSON ファイル。 | 共有の JSON のジョブ保存先と、アトミックな確保。 | session のログのイベント。fork は履歴を残し、リマインダーは落とす。 |
| **方法: wakeup** | 発火した prompt はキューに入り、turn のあいだに走る。 | 期限の来たジョブは並行に走り、チャットに届ける。 | 期限の来た作業はアイドルを待ってから turn を 1 つキューに入れる。少なくとも 1 回。 |

---

## 失敗モード

- **二重発火。** tick が速いと、同じ cron の分に 2 回以上一致しうる。最後に発火した分を記録する。
- **多数の schedule が同時に発火する。** 繰り返しの task に決定的な jitter を足す。
- **永続的と常時稼働は違う。** ローカルの永続的な schedule が生き延びるのは再起動までである。オフラインでの発火には遠隔のトリガーか OS のタイマーを使う。
- **cron の式が不正。** 作成時に検証し、読み込んだ不正なエントリは飛ばす。
- **loop が忙しい。** prompt をキューに入れ、turn のあいだに取り出す。
- **アラート疲れ。** tick のたびに報告する heartbeat は、ユーザーに無視することを教えてしまう。何を送る価値があるかは prompt に判断させ、そうでなければ黙らせる。
- **tick と tick のあいだのイベント。** 時計は状態を標本抽出する。2 回の tick のあいだに現れて戻った変化は見えない。ログかカーソルを読むか、情報源をプッシュに移す。

---

## 実行

[`src/`](src/) は 13 を引き継ぎ、次を追加します。

- [`scheduler.py`](src/scheduler.py): scheduler、発火のキュー、繰り返しの再セット、一度きりの削除、永続的な JSON の保存先、channel への配信 (`deliver`、`SILENT`)。
- [`test.py`](src/test.py): 偽の時計を使って、一度きり、繰り返し、読み直し、配信の挙動をテストします。
- [`demo.py`](src/demo.py): 1 秒後の prompt を予約し、新しい turn として走らせ、答えをコンソールの channel に届けます。

loop は変わりません。scheduling は loop の外から turn を始めます。

```bash
python sections/14-scheduling/src/test.py         # offline checks, no key
uv run python sections/14-scheduling/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `tools/ScheduleCronTool/`, `tools/RemoteTriggerTool/`, `tools/SleepTool/`, `utils/cronScheduler.ts`, `hooks/useScheduledTasks.ts`, `utils/queueProcessor.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `cron/scheduler.py` (`tick`, `_resolve_cron_disabled_toolsets`), `cron/jobs.py` (`_jobs_lock`, `claim_dispatch`), `hermes_time.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/schedule/schedule/src/runtime.ts`, `packages/schedule/schedule/src/persistence.ts`, `packages/schedule/schedule/src/tools.ts`,
  `docs/subsystems/schedule.md`, `docs/tool-catalog.md`.
- [learn-claude-code · s14_cron_scheduler](https://github.com/shareAI-lab/learn-claude-code): section の枠組み。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter4.md`, 中国語の原著が正典。
  判断を伴う heartbeat の起床、アラート疲れ、時刻で駆動するトリガーの限界。
