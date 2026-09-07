# 18 · Autonomy

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> 人間の prompt なしで loop を回します。手が空いたらボードを見て、着手できる task を確保し、それをこなします。

autonomy とは、section 1 (agent loop) を、各 turn を始める人間の prompt なしで走らせることです。

チームを spawn すると、まず思い付く設計は、lead が各 worker に次の task を手渡す形です。

これは規模が大きくなると持ちません。未着手の task が 10 個あれば手作業の割り当てが 10 回必要になり、lead がボトルネックになります。

終わった瞬間に手が空く worker も、いま読み込んだばかりの context を無駄にします。

直し方は自己組織化であって、中央からの割り当てではありません。

とはいえ中央からの割り当ても実在する設計で、公開されたマルチ agent の研究の多くはそちらを扱っています。
マネージャー型では、各 child agent が tool として登録され、1 体のマネージャーがすべてのサブタスクを配ります。
マネージャーは計画全体を持つので、作業を順序付け、重複する task を落とし、実行を早めに打ち切ることができます。
その代わり、すべての task に 2 回触れます。配るときと、結果を読むときです。
2 つの設計は、支払うものが違います。マネージャーはグローバルな順序を 1 つ与えますが、どの task もマネージャーの番を待ちます。
ボードはスループットを与えますが、確保が陳腐化するのでロックが要ります。このセクションではボードを作ります。

autonomy は、手の空いた agent に次のことをさせなければなりません。

1. やることがないと気付く (作業のフェーズが `end_turn` に達した)。
2. 共有ボードを見て、誰も所有しておらず、何にも塞がれていない task を探す。
3. 他の手の空いた agent と競合せずに 1 つ確保する。
4. 確保した task で loop に入り直し、ボードが空になるまで繰り返す。

これを省くと、どの agent も操り人形です。人間か lead が次の prompt を押し込むのを待つので、スループットは dispatcher が prompt を配る速さで頭打ちになります。

---

## 仕組み

![Mechanism diagram](assets/18-autonomy.png)

外側の loop が agent loop を包みます。

内側の loop は section 1 の普通の `while` です。`end_turn` に達しても、agent は戻りません。ポーリングに入ります。

ポーリングは 2 つの経路を drain します。この agent 宛のメッセージが届く名指しの inbox (section 16) と、手の空いた agent なら誰でも確保できる task の並ぶ名指しでないボード (section 12) です。

確認する順序には優先度があります。まず shutdown の要求、次に inbox のメッセージ、そのあとにボード上の task です。

見つかったものが次の prompt になり、内側の loop がもう一度走ります。

- 内側の loop はモデルの `stop_reason` で終わります。section 1 と同じ信号です。
- ポーリングは shutdown を最初に確認するので、停止がピアからのメッセージに埋もれることはありません。
- 確保は、ロックのもとでの読み取り、検査、書き込みです。所有者がなく塞がれていない task を選び、他の agent より先に所有権を書き込みます。
- task を確保できるのは、その依存が `completed` のときだけなので、塞がれた作業を確保する agent はいません。

shutdown の要求とその確認は section 17 の protocol なので、停止は kill ではなくハンドシェイクです。

### 新規: アイドル時のポーリング

`autonomy.py` は外側の loop と、ポーリング 1 巡分を足します。`next_action` は inbox を一度 drain し、見つかった最初のものを優先度順に返します。

```python
def next_action(proto, team, store, me):               # src/autonomy.py
    inbox = team.drain(me)
    shutdown = next((m for m in inbox if _is(m, "shutdown_request")), None)
    if shutdown is not None:                            # checked first, so chat cannot starve a stop
        proto.reply(shutdown, "shutdown_approved")
        return ("shutdown", shutdown["content"].get("reason"))
    chat = [m for m in inbox if isinstance(m["content"], str)]
    if chat:
        chat.sort(key=lambda m: m["from"] != "lead")   # lead before peers; sort is stable
        return ("message", _fold(chat))                # section 16's shared fold helper
    task = claim_next(store, me)                        # else claim the next ready task
    return ("task", task) if task is not None else None
```

- 返るのは、shutdown (確認して停止)、織り込んだチャット、確保した task のうち最初のものです。
- shutdown はチャットより先に確認するので、ピアからの通信量で停止がリソース不足に陥ることはありません (section 16)。
- `claim_next` は pending で所有者のいない最初の task を確保します。`TaskStore.claim` は塞がれた作業を拒否し、確保を直列化します (section 12)。
- `None` はアイドルを意味します。外側の loop はスリープして、もう一度ポーリングします。

### ロックのもとでの確保

ポーリングは task を提案し、誰が取るかはロックが決めます。`claim_next` はボードを古い順に走査し、所有者のいない pending な最初の task を提案します。

```python
def claim_next(store, me):                             # src/autonomy.py
    for t in store.list():                             # oldest first
        if t["status"] == "pending" and t["owner"] is None:
            got = store.claim(t["id"], me)             # read, check, write under a lock
            if got["ok"]:
                return got["task"]
            # not ok: another agent won it, or it just became blocked; try the next
    return None
```

`claim_next` の中の検査は手がかりにすぎません。手の空いた agent 2 体が、同じ task を同時に所有者なしと読むことがあります。決着を付けるのは、ロックのもとで動く `TaskStore.claim` (section 12) です。

```python
def claim(self, tid, owner):                           # src/tasks.py, section 12
    with self._lock():                                 # fcntl.flock: one claimer at a time
        task = self.get(tid)
        if task["owner"] is not None:                  # someone already won: back off
            return {"ok": False, "reason": "already_claimed"}
        unmet = [b for b in task["blockedBy"]
                 if (self.get(b) or {}).get("status") != "completed"]
        if unmet:                                       # a dependency is not done yet
            return {"ok": False, "reason": "blocked"}
        task["owner"], task["status"] = owner, "in_progress"
        self._write(task)
        return {"ok": True, "task": task}
```

- ロックは読み取り、検査、書き込みを 1 つの不可分なステップにするので、書き込みの前に検査が陳腐化することはありません。
- 負けた側はロックの中で読み直し、`owner` が設定済みなのを見て `already_claimed` を受け取ります。`claim_next` は次の task へ進みます。
- 塞がれた task もここで拒否されるので、依存が `completed` でない作業を確保する agent はいません。
- 共有状態を 2 つの thread が奪い合うのはここだけです。ポーリングの残りはローカルな処理です。

### 組み込み方

外側の loop は `run_turn` を外側から包むので、loop と subagent の経路は変わりません。

```python
def run_teammate(team, store, me, lead, work):         # src/autonomy.py
    proto, prompt, claimed = Protocol(team, me), None, None
    while True:
        if prompt is not None:
            work(prompt, claimed)                      # inner loop (section 1) does the claimed task
            prompt, claimed = None, None
            team.send(me, lead, {"type": "idle", "reason": "available"})
        action = next_action(proto, team, store, me)   # poll: shutdown, message, or task
        if action is None:                             # idle: sleep, then poll again
            time.sleep(POLL_INTERVAL); continue
        kind, payload = action
        if kind == "shutdown":
            return "shutdown"
        if kind == "task":
            prompt, claimed = task_prompt(payload), payload
        else:
            prompt = payload
```

- この `run_teammate` は section 17 のものに、ポーリング元をもう 1 つ足したものです。足したのは task board です。shutdown (section 17) とチャット (section 16) は変わりません。
- `work(prompt, claimed)` は確保した task について内側の loop を `end_turn` まで 1 回走らせ、そのあと agent は手が空いたと通知します。
- 確保した task が次の prompt になります。ポーリングが何も見つけなければ、worker は自分で停止を決めます。
- その停止はどちらのモードでもかまいません。shutdown のハンドシェイク (section 17) が来るまでアイドルでいるか、有限のボードで空のポーリングが一定回数続いたら店じまいするかです。
- ここで動く worker は 1 体ですが、loop は agent ごとにあります。実運用のチームでは、1 つの共有ボードと inbox の集合の上で、lead の loop と多数の worker の loop が同時に走ります。
- lead が能動的に踏むステップは 1 つだけです。tool を呼んでチームと作業を組み立て、それで終わりです。
- `TeamCreate` と `SpawnTeammate` は section 16 の tool です。`TaskCreate` はボードへ投稿します (section 12)。
- `SpawnTeammate` は `runtime.start(...)` です (section 13)。lead の tool 呼び出しが、worker の autonomy loop を thread 上で起動します。
- spawn したあとは、仕事を取りに行くことも、いつ止まるかを決めることも、各 worker 自身の仕事で、lead やスクリプトの仕事ではありません。メインプロセスは worker が店じまいするのを待つだけです。
- チームを組むこと、spawn すること、ボードへ投稿することは、モデルの判断です (section 16 と 12)。自律的な確保が section 18 の追加分です。

### さらに読む

ここから先は `src/` にはありません。ai-agent-book と公開された研究に基づく内容で、表に挙げたシステムでの裏付けは取れていません。

**忙しい worker に尋ねる。** ポーリングは worker に次に何をするかを教えます。走っている worker の様子を lead に教えることはありません。

**状態を問い合わせる呼び出しが弱い理由。** tool 呼び出しの途中にいる worker は、メッセージを聞いていません。だから呼び出しは、ハングするか、空のまま返ります。
本当に詰まっている worker こそ、答えない worker です。

**うまくいく 3 つの方法。** 1 つ目は worker の協力が要り、最後の 1 つは何も要りません。

1. メッセージで尋ねる。状態の要求を inbox へ入れます (section 16)。worker は次のポーリングで答えます。正確ですが、worker がまだポーリングしている間だけ有効です。
2. 取り決めた進捗ファイルを読む。worker は双方が知っているパスへ、1 ステップにつき 1 行を追記します。読む側が作業を妨げることはありません。
3. 保存された trajectory を追う。ランタイムはすでにすべての turn をディスクへ書いています (section 13)。lead はその turn を読み、worker は何もしません。

**停滞を見つける。** 後ろの 2 つの方法なら、それはただで付いてきます。ファイルが最後に書かれた時刻を見ます。新しい書き込みがないなら、新しい進捗もないからです。
しきい値を 1 つ決めれば、それが判断になります。遅い tool 呼び出しは走っている間なにも書かないので、しきい値は想定される最も遅い呼び出しより上に置きます。それを超えたら、その worker は詰まっているとみなします。
そのしきい値が、忙しく見えたまま詰まる失敗モードに欠けていたトリガーです。lead は永久に待つ代わりに、task を取り戻すか、shutdown のハンドシェイク (section 17) を始められます。

**プールへの予算。** ポーリングの中に、確保をやめろと worker に伝えるものは何もありません。手の空いた worker は、もう 1 つ task を取ります。
だから実行が終わるのは、作業が終わったときではなく、予算が尽きたときです。

**何を配るか。** ある公開されたマルチ agent システムでは、実行の出来の差のおよそ 80% が token の使用量だけで説明できました。
つまり、割り当てるべき単位は turn ではなく token です。ボードと worker のプールには、4 つのつまみが付きます。

- task ごとの予算。各 task が自分のステップ上限と token 上限を持ち、投稿時に書き込まれます。そうすれば、暴走した task 1 つが実行全体を吸い尽くすことはありません。
- 同時実行の上限。同時に `in_progress` にある task の数を制限します。ボードはすでにその数を数えているので、上限を超えた確保は単に失敗します。
- モデルの配置。考えることが最も難しいところに、最も強いモデルを置きます。計画の質が結果を決めるので、lead がそのモデルを取り、定型作業の worker は安いモデルで動きます。
- プリエンプション。予算を超えた worker や、しきい値を超えて停滞した worker は、task をボードへ手放します。次に確保した誰かは、きれいな状態から始めます。

**worker に自分の予算を見せる。** 残りがどれだけあるかを知っている agent は、単に上限を増やしてもらった agent とは違う使い方をします。
この結果は本の著者自身の実験から来ているので、裏付けは 1 ソースです。写して使う数字ではなく、試すべきことだと考えてください。

---

## システム別

手の空いた agent が、自分の仕事をどう見つけてどう確保するかを示します。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **利点** | dispatcher のボトルネックがありません。ウォッチャーは、他所で作られた task も拾います。 | 無人での実行が読みやすく、継続用の状態も永続します。 |
| **欠点** | 手の空いた agent 2 体が同じ task に目を付けることがあるので、競合はロックで決着させます。 | agent 1 体につきゴール 1 つです。ゴールを満たしたかどうかはモデル自身が判断します。 |
| **理由** | すべての task を配る lead はボトルネックになります。 | autonomy はモードではなく、予算の付いた permission のレベルです。 |
| **方法: アイドル時の挙動** | 500ms のポーリング。shutdown、次に未読メッセージ、そのあとに確保。 | 完全にアイドルになると、次のラウンドを予約して prompt を 1 つキューに入れます。 |
| **方法: 仕事の確保** | 塞がれていない task の所有権をロックのもとで書き込むので、確保に成功するのは 1 体です。 | ゴールのその時点のリビジョンに対して次のラウンドを予約し、合わなければ失敗します。 |
| **方法: 自己組織化** | worker がボードから仕事を取りに行きます (section 12)。lead はまとめる側で、振り分けはしません。 | ボードはありません。agent は自分のゴールを続け、fan-out には上限があります。 |

---

## 失敗モード

- **確保の競合。** 2 体の agent が同じ task を所有者なしと読み、どちらも確保して、片方の作業が捨てられます。検査と書き込みを不可分にするファイルロックの中で確保 (section 12)。
- **おしゃべりによるリソース不足。** ピアのおしゃべりが shutdown の要求を埋め、止まるべき agent がポーリングを続けます。通常のメッセージより先に shutdown を確認 (section 16)。
- **塞がれた作業の早すぎる確保。** 依存が終わっていない task を agent が確保し、そのまま止まります。`blockedBy` に未解決の id を持つ task は飛ばす (section 12)。
- **compaction 後のアイデンティティの喪失。** 長く動くメンバーが途中で自動 compaction され (section 8)、自分の役割を忘れます。system prompt を保持し、役割が残るようにする。
- **忙しいまま詰まる、あるいはアイドルのまま詰まる。** `end_turn` に達しないフェーズは task を解放しませんし、出口のないポーリングは空回りします。停止の信号で終える (section 1)。ポーリングのたびに中断を確認。
- **見逃された停滞。** worker が task を握ったまま、忙しそうに見えて何も進まず、ボードはそれを解放しません。進捗ファイルが最後に書かれた時刻を見て、task を取り戻す。
- **上限のないプールの支出。** 手の空いた worker が確保を続けるので、実行が終わるのは予算が尽きたときだけです。すべての task にステップと token の上限を与え、同時に走る数にも上限を置く。
- **プリエンプションでの二重書き込み。** 手放された task が、古い worker の動作中に再確保され、2 体の agent が同じファイルを書きます。停止が ack されてから解放 (section 17)。

---

## 実行

[`src/`](src/) は 17 を引き継いだうえで、次を追加します。

- [`autonomy.py`](src/autonomy.py): section 12 のボードの上に載る外側の loop とアイドル時のポーリング (各 worker を起動するのは section 16 の `SpawnTeammate`)。
- [`test.py`](src/test.py): worker 1 体での仕組み、`TeamCreate` の確認、確保の競合を強制的に起こす検査 (16 thread、task 1 つ、勝者 1 体)、thread 化したパイプライン、spawn tool の確認。
- [`demo.py`](src/demo.py): lead が 1 ステップ進め (`TeamCreate`、`TaskCreate`、`SpawnTeammate`)、そのあと worker がボードから task を取りに行き、ボードが空になったら自分で止まります。

仕組みの節に出てくる worker 1 体の `run_teammate` は、説明のための縮約版です。

実運用のチームでは、1 つの共有ボードと inbox の集合の上で、lead の loop と複数の worker の loop が同時に走ります。

section 13 が作業を thread 上で起動し、section 12 と section 16 のファイルロックが、競合のもとでも共有状態を安全に保ちます。

同時実行のデモと確保の競合のテストが、それを組み上げています。

```bash
python sections/18-autonomy/src/test.py         # offline checks, no key
uv run python sections/18-autonomy/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code autonomy](https://github.com/yasasbanukaofficial/claude-code):
  `utils/swarm/inProcessRunner.ts` (`runInProcessTeammate`, `waitForNextPromptOrShutdown`, `findAvailableTask`, `tryClaimNextTask`, `sendIdleNotification`).
- [Claude Code claim and watch](https://github.com/yasasbanukaofficial/claude-code):
  `utils/tasks.ts` (`claimTask`, `claimTaskWithBusyCheck` under `proper-lockfile`), `hooks/useTaskListWatcher.ts`, `coordinator/coordinatorMode.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/bundle/headless/README.md`, `packages/goal/goal-round-driver/README.md`, `packages/workflow/tool-ralph/README.md`,
  `docs/subsystems/permission-presets.md`, `docs/subsystems/goal.md`.
- [learn-claude-code · s17 autonomous agents](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book · chapter 10](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  why a pull-style status query is weak, progress files and trajectory tailing, mtime stall detection, the manager pattern's central assignment,
  and team resource scheduling (per subtask budgets, concurrency caps, model placement, preemption).
  The budget-awareness result comes from the book's own experiment, so it is single-source.
- [Plan-and-Act](https://arxiv.org/abs/2503.09572) (Erdogan et al., 2025): splitting a planner from an executor, with plan quality as what drives the outcome.
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) (Anthropic, 2025):
  token use alone explains about 80% of the variance in performance, with tool call count and model choice next.
