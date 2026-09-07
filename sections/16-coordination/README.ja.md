# 16 · Coordination

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> lead がタスクに見合った規模のチームを組み、各メンバーを専用の thread 上に spawn し、メンバーどうしは共有の inbox 越しに会話します。

agent 1 つが持つのは、context window 1 つと、進行中の作業の筋 1 本だけです。大きな仕事では、複数の agent が同時に動く必要がよくあります。

subagent は焦点の絞られたタスクをこなせますが、一度きりの subagent は動き出したあとで方向を変えるのが難しいです。

agent が 1 つ増えるごとに token を消費しますし、2 つの agent が同じファイルを別々の方向へ編集してしまうこともあります。
そこで最初に決めるのはチームの形です。agent を何体にするか、context を共有するか、誰が誰に指示するか、の 3 点です。

連携する agent には、互いを spawn する手段、安定した名前、会話用の inbox、そして permission の要求を人間へ戻す経路が必要です。

coordination に必要な機能は次のとおりです。

1. agent に安定したアドレスを与える。
2. lead がタスクに合わせてチームの規模を決め、チームを組めるようにする。
3. lead が各メンバーを専用の thread 上に spawn できるようにする。
4. 各メンバーが自分の inbox を取りに行き、スクリプトに動かされずに自分で動けるようにする。
5. ゲートのかかった操作を人間の承認者へ引き上げる。

この層がないと、大きな仕事は直列のままになるか、協調できない worker へ分裂してしまいます。

---

## 仕組み

![Mechanism diagram](assets/16-coordination.png)

各 agent は自分の inbox を持ちます。メッセージを送るとは、受信側の inbox へ書き込むことです。配送が成立するのは、受信側が自分の inbox を drain したときです。

チームの規模と名前は、スクリプトに直書きされるのではなく、実行時に lead のモデルが決めます。
lead は `TeamCreate` を呼んでタスク用のチームを組み、それから各メンバーを spawn します。

lead が手作業でメンバーを起動することはありません。lead は `SpawnTeammate` を呼び、harness がそのメンバーの loop をバックグラウンドの thread で走らせます (section 13)。
あとはメンバー自身が自分の inbox を取りに行って動くので、スクリプトは誰も動かしません。

このデモに中央のブローカーはありません。あるのは、名前、inbox のパス、メッセージの形についての共通の取り決めだけです。

- 各 agent は inbox を 1 つ持ちます。
- メッセージは送信者、受信者、内容から成ります。
- lead は `TeamCreate` を呼んで名簿の規模を決めて作り、続く `SpawnTeammate` が各メンバーを起動します。
- lead は `SpawnTeammate` でメンバーを spawn し、そのメンバーは専用の thread で動きます。
- `to="*"` は送信者を除く全メンバーへブロードキャストします。
- 送信側は書き込んだら戻ります。返事を待ってブロックすることはありません。
- メンバーはポーリングのたびに自分の inbox を取りに行き、新しいメッセージを次の turn に織り込みます。
- permission の要求も同じ経路を使います。

### 新規: チームを組む

`TeamCreate` は、lead が名簿の規模を決めて作るために呼ぶ tool です。この tool は 1 枠だけの保持箱を埋め、harness は各メンバーを spawn するときにそこを読み返します。

```python
def team_tools(root, me, formed):                      # src/mailbox.py
    def create(a):
        members = list(dict.fromkeys([me, *a["members"]]))   # the lead joins its own team
        formed["team"] = Team(root, members)                 # the tool call sizes and forms the team
        return f"team created: {', '.join(members)}"
    ...                                                # SendMessage stays inert until the team exists
```

- スクリプトは規模も名前も固定しません。どちらも lead がタスクから選びます。
- `SendMessage` は `TeamCreate` が走るまで何もしないので、lead はチームと話す前に必ずチームを組みます。
- `formed` は 1 枠だけの保持箱です (ponytail: team registry のプロセス内での代用品。名簿ファイルで裏打ちすれば、別プロセスのメンバーも参加できます)。

### 新規: メンバーを spawn する

`SpawnTeammate` は lead のモデルが呼ぶ tool です。harness はそのメンバーの loop を、section 13 のランタイム上の専用 thread で起動します。

```python
def teammate_tools(runtime, spawn_worker):             # src/mailbox.py
    def spawn(a):
        runtime.start(lambda: spawn_worker(a["name"]))  # section-13 thread runs the teammate's loop
        return f"spawned teammate {a['name']}; it runs on its own thread and pulls its own work"
    return [Tool("SpawnTeammate", spawn, is_read_only=True, ...)]
```

メンバーの loop は `serve_mailbox` です。inbox を取りに行き、動き、それを繰り返します。spawn された thread 上で動くので、メンバーはスクリプトではなく自分の判断で反応します。

```python
def serve_mailbox(team, me, work, *, poll=0.05, max_idle_polls=None):   # src/mailbox.py
    while True:
        chat = [m for m in team.drain(me) if isinstance(m["content"], str)]
        if chat:                                        # a message to act on
            folded = "\n".join(f"<message from={m['from']!r}>{m['content']}</message>" for m in chat)
            work(folded)                                # one inner loop (section 1) on the message
            continue
        time.sleep(poll)                                # empty: poll again
```

- `spawn_worker(name)` はアプリ側の thunk で、そのメンバーのために `serve_mailbox` の loop を 1 本走らせます。
- メンバーは drain しながらメッセージを消費するので、1 通のメッセージはちょうど一度だけ配送されます。
- 行儀のよい停止手段はまだありません。thread はデーモンで、プロセスとともに死にます。section 17 でシャットダウンのハンドシェイクを追加します。
- `max_idle_polls` はアイドル待ちに上限を与え、デモやテストが終わるようにします。実運用のメンバーはプロセスが止まるまでポーリングを続けます。

### inbox と permission の経路

context が別々の agent が会話する方法は 2 つあり、これはプロセスが持つ 2 つと同じです。
共有メモリでは、全員が 1 か所を読み書きし、同じ状態を見ます。
メッセージパッシングでは、送信者が受信者 1 つ宛てにコピーを送り、両者は何も共有しません。
この 2 つを運ぶ経路は 3 つあります。tool の呼び出し引数は一方向で、返事の経路がありません。ファイルは再起動しても残りますが、ロックが要ります。
メッセージバスはアドレスと順序を足しますが、再起動を越えて残るのはディスクへ書く場合だけです。
ここでの inbox はロック付きのファイルなので、共有ファイルシステム上のメッセージパッシングです。
team memory (section 9) と task board (section 18) が共有メモリ側にあたります。
たいていのチームは両方を使います。作業を割り振るのはメッセージ、メッセージより長く残る事実は共有メモリです。

`mailbox.py` は、名前付き inbox の集まりである `Team` を実装します。

```python
def send(self, frm, to, content):                      # src/mailbox.py
    targets = [m for m in self.members if m != frm] if to == "*" else [self._check(to)]
    with self._lock():                                 # serialize concurrent senders
        for t in targets:
            inbox = self._read(t)
            inbox.append({"from": frm, "to": t, "content": content})
            self._path(t).write_text(json.dumps(inbox))
```

- `_check` は、未知の名前がパスになる前に拒否します。
- ロックは読み取り、変更、書き戻しを直列化するので、送信が同時に起きてもメッセージが落ちません。
- `drain` は inbox を 1 つ読み取り、そのうえで空にします。

permission の引き上げは、承認者の一実装です。ゲートのかかった呼び出しを、同じ経路で人間へ渡します。

```python
def bubbling_approver(team, me, lead, human=None, timeout=0.0, poll=0.05):
    def approve(name, args):                            # approver for an agent with no human UI
        team.send(me, lead, {"kind": "permission_request", "tool": name, "args": args})
        if human is not None:                           # the lead routes it to its approval UI
            team.send(lead, me, {"kind": "permission_response", "tool": name, "ok": human(name, args)})
        deadline = time.time() + timeout
        while True:
            resp = [m["content"] for m in team.drain(me)
                    if isinstance(m["content"], dict) and m["content"].get("kind") == "permission_response"]
            if resp:
                return bool(resp[-1]["ok"])
            if time.time() >= deadline:
                return False                            # nobody answered in time: default deny
            time.sleep(poll)
    return approve
```

1. メンバーがゲートのかかった tool 呼び出しに当たりますが、自分の loop にはキーボードの前の人間がいません。
2. 承認者は `permission_request` を lead の inbox へ送ります。
3. lead はそれを自分の承認 UI (ここでは `human` コールバック) へ回します。
4. 判定は `permission_response` としてメンバーの inbox に返ります。
5. メンバーはその応答を読み、ゲートへ許可か拒否を返します。

ゲートは変わらず `approver(name, args)` を呼ぶだけで、中身は変わりません。答えは直接の呼び出しではなく inbox のメッセージとして届くので、エスカレーションは同じ経路を使い回します。

`human` がない場合、答えは別のどこか (別 thread の lead、チャットプラットフォーム上の人間) から来なければなりません。
承認者は `timeout` まで自分の inbox をポーリングし、そのあと拒否します。答えのない permission は拒否であり、停滞でも許可でもありません。
これは hermes-agent の clarify gateway と同じ形です。あちらでは `wait_for_response` が、チャットアダプタが答えるか timeout が発火するまで agent の thread をブロックします。

### 組み込み方

デモが動かすメインの agent は 1 体です。lead が 1 ステップ進めれば、あとはメンバーが自分で動きます。

```python
def spawn_worker(name, formed, model):                 # src/demo.py, module level
    team = formed["team"]                              # whatever the lead formed with TeamCreate
    ...                                                 # build the teammate's tools
    return mailbox.serve_mailbox(team, name, work)      # the teammate pulls its own inbox

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
```

- スクリプトが与える入力は lead のゴールだけです。lead は `TeamCreate` でチームの規模を決め、`SpawnTeammate` で各メンバーを spawn し、`SendMessage` で仕事を任せます。
- `demo()` が走らせる `run_turn` は 1 つ、lead のものだけです。メンバー自身の `run_turn` は `spawn_worker` の中にあり、spawn tool を通してのみ到達します。
- 各メンバーは section 13 の thread 上で `serve_mailbox` を走らせ、inbox を取りに行き、作業し、返信します。返信の数を決めるのは lead で、メインプロセスは待つだけです。
- `loop.py` は汎用のままです。メッセージの織り込みとポーリングの loop は coordination の仕事で、`run_turn` の中ではなくこのラッパーで行います。
- permission のゲートは変わりません。ゲートのかかった呼び出しは、これまでどおり lead へ引き上がります。

### さらに読む

ここから先は `src/` にはありません。ai-agent-book と公開されたマルチ agent 研究に基づく内容で、表に挙げたシステムでの裏付けは取れていません。

**チームが agent 1 体に勝つのはどんなときか。** 2 体目の agent を足すのは、1 体目には見えなかった何かを持ち帰るときだけにします。
テスト結果、スクリーンショット、取得したページ、動いているシステムからの答えなどです。これが新しい情報です。
同じテキストをもう一度読んで投票するだけの agent は、新しい情報を何ももたらしません。token を使うだけです。

これを外したときの代償は、公開された 2 つの結果が示しています。Tran と Kiela は、agent 1 体とチームに同じ thinking token の予算を与えました。
彼らが計測したタスクでは、1 体の agent が引けを取りませんでした。Anthropic は、自社のリサーチチームがチャット 1 turn の約 15 倍の token を使うと報告しています。
そこまで高いチームは、何かを持ち帰らなければ割に合いません。

**context を共有するか、分離するか。** 2 体の agent は、履歴を 1 つ共有するか、別々に持つかのどちらかです。

- **共有。** 次の agent がすべてを引き継ぐので、何かを詰め込む必要がなく、事実が抜け落ちることもありません。
  代償として、同時に動けるのは 1 体だけで、1 つの window にチーム全員の履歴が載ります。
- **分離。** 各 agent が自分の window を持ち、必要なものを自分で言わなければなりません。agent は同時に動けますし、1 体の混乱はその window の中で止まります。
  代償として、受け渡しのたびに内容を書き出す必要があります。

サブタスクが少なく、履歴が 1 つの window に収まり、どうせ手順が順番どおりに進むなら共有を選びます。それ以外は分離します。
このリポジトリは分離を採ります。subagent は空の状態から始まり (section 6)、メンバーは自分の inbox しか読みません。

**3 つのトポロジ。** 分離した agent でも、誰が誰と話すかは知っている必要があります。形は 3 つです。

- **ピア。** 対等な立場の agent どうしがメッセージを送り合います。レビューやクロスチェックはここに向きます。
- **マネージャー。** 1 体の lead が仕事を分け、配り、返ってきたものをまとめます。child agent が返すのは要約で、履歴ではありません。
- **分散。** lead がいません。各 agent が、次に仕事を渡す相手を自分で選びます。

このセクションで作るのはマネージャー型です。lead が全員分の計画を立てるので、分け方が悪ければ悪いままで、worker の側では直せません。
これが、lead に最も強いモデルを、worker に安いモデルを与える理由になります。

**分散型チームはどう仕事を回すか。** lead がいなくても、仕事は次の agent に届かなければなりません。公開された設計が 3 つ、回し方も 3 通りです。

- **MetaGPT** はすべてのメッセージをプールへ投稿します。各ロールは自分が扱うメッセージ型を購読するので、送信者が受信者を名指しすることはありません。
- **AutoGen** のグループチャットは transcript を 1 つ保ち、中央のセレクタが次に話す相手を選びます。セレクタが同じ 2 体ばかり選び続けると、チャットはライブロックします。
- **OpenAI Swarm** は受け渡しのたびに tool 呼び出しを挟み、仕事が持ち手を替えられる回数に上限を置くので、受け渡しの連鎖は必ず終わります。

**ファイルツリーの 4 つの領域。** agent は名前で互いを見つけます。状態はパスで見つけます。この本はツリーを 4 つの領域に分けます。

- **プライベートなスクラッチパッド。** agent 1 体の下書きです。他の誰も読まないので、調整するものがありません。
- **共有ワークスペース。** リポジトリ、task board、team memory です。全メンバーがここに書くので、衝突が起きるのはここです。
  ロックか、agent ごとの worktree (section 15) が要ります。
- **外部マウント。** チームが作ったのではないデータで、チェックアウトやデータセットなどです。ここへの書き込みは、チームの外側の何かを変えます。
- **読み取り専用の組み込み。** skill、prompt、tool の定義です (section 7 と 2)。実行中は変わらないので、どの agent も同じコピーを見ます。

状態を置く領域を間違えると、それは coordination のバグとして返ってきます。2 体の agent が 1 つのファイルを編集したなら、そのファイルは共有ワークスペースにあったということです。
同じ事実を 3 回送ったなら、それは team memory へ入れるべきだったということです。

**受け渡しは何を運ぶか。** メンバーには lead のチャットが見えないので、「落ちているテストを直して」と言われても動きようがありません。受け渡しのパケットは 3 つのものを運びます。

1. タスク。受け手が自分で確認できる受け入れ基準を付けます。
2. すでに確認済みの事実と、成り立っている制約。受け手が同じことを調べ直したり、制約を壊したりしないためです。
3. ファイル、ログ、ブランチへのパス。

送り手の生の履歴は含めません。長いうえに行き止まりだらけで、受け手に送り手の失敗を読ませることになります。

もう 1 つの選択肢が context を共有する受け渡しで、これはパケットを省きます。1 体の agent が制御を別の agent へ渡し、履歴もまるごと付いていくので、置き去りになるものがありません。
本ではロール間で制御を移す tool を使ってこれを示しています。ただし著者自身の実験なので、1 つのソースとして扱ってください。
代償として、制御を持てるのは 1 体だけなので、同時には何も動きません。パケットは書く手間がかかりますが、その代わりに並列に走る作業が手に入ります。

> **次:** ここでのメンバーは行儀のよい停止手段を持たないデーモンで、メッセージにしか反応しません。
> section 17 はシャットダウンのハンドシェイクを追加し、lead がメンバーをきれいに終わらせられるようにします。
> section 18 は共有の task board を追加し、手の空いたメンバーがメッセージを待たずに自分で仕事を確保できるようにします。

---

## システム別

ある設計が、協調する agent をどう spawn し、仕事をどう分散させるかを示します。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **利点** | ピアどうしが直接話します。ファイルの inbox はプロセスをまたげます。 | child agent はどのインターフェースからでも一時停止と割り込みができます。 | スクリプトが厳しい上限のもとで多数の child agent へ fan out します。 |
| **欠点** | ポーリングとロックのコストがかかります。メモリ上の inbox はプロセスとともに消えます。 | ピア間の inbox がありません。clarify は自分の thread をブロックします。 | child agent どうしは話せません。送信しても返事は返りません。 |
| **理由** | ピアには inbox と、人間の承認者への経路の両方が要ります。 | 連携は親から child へ、という向きに保ちます。 | 連携とは所有関係です。各 child agent には親が 1 体います。 |
| **方法: メンバーの形** | プロセス内かリモートで、それぞれが自分の loop を走らせます。 | thread 上に委譲された child agent で、一時停止フラグを持ちます。 | モデルが書いたスクリプトが spawn し、一部は常駐します。 |
| **方法: 経路** | SendMessage が inbox へ書き込み、ブロードキャストもできます。 | 完了キューとゲートウェイ呼び出しです。 | 親から child への一方向のみ。child は report tool で答えます。 |
| **方法: 共有メモリ** | チームの task リストと team memory のディレクトリです。 | 共有の session DB と、系統を示すマーカーです。 | 親のディレクトリです。fork は親の完了済み turn もコピーします。 |
| **方法: permission の引き上げ** | リモートからの要求がローカルの承認プロンプトになります。 | clarify はチャットへ回り、child agent は自動で拒否か許可します。 | 要求は親の連鎖をさかのぼります。 |

---

## 失敗モード

- **メッセージ消失の競合。** 2 つの送信者が 1 つの inbox へ同時に書き込み。読み取り、変更、書き戻しをロック。
- **ピア間のデッドロック。** agent どうしが待ち合わせ。送信をブロックせず、メッセージをキューに入れて turn の合間に drain。
- **permission の停滞。** メンバーに人間向けの UI がない。要求を lead へ引き上げる。
- **create の前に spawn。** lead が `TeamCreate` の前に spawn や送信を行い、名簿が存在しない。チームができるまで両方を不活性に保つ。
- **孤児化したメンバー。** spawn されたメンバーが、仕事を終えたあともポーリングを続ける。アイドル待ちに上限を置くか、section 17 のハンドシェイクで停止。
- **曖昧な agent 間メッセージ。** メンバーには lead のチャットが見えない。パケットを送る。タスク、受け入れ基準、確認済みの事実、成果物のパス。
- **チャットを memory 代わりに使用。** 長く残る共有の事実は team memory へ。
- **ビザンチンなメンバー。** 壊れた agent はクラッシュしません。間違った答えを、自信ありげな口調で返します。
  やり直させても、同じ証拠で投票させても、返ってくる答えは同じです。モデルの外側の何かと突き合わせて初めて見つかります。
- **共有ファイルでの更新消失。** 2 体の agent が 1 つのファイルを読み、どちらも書き戻す。先の書き込みは消えます。
  書き込みをロックするか、バージョン番号を保存し、一致しなければやり直す。
- **意味レベルの衝突。** どちらの書き込みも問題なく適用され、それでも結果が壊れている。片方が関数名を変え、もう片方が古い名前への呼び出しを追加した場合など。
  2 体が同じものを担当しないように仕事を分けるか、1 か所でマージする。
- **エラーの連鎖。** ある agent が事実を取り違える。次の agent がそれを繰り返し、その次もまた繰り返し、その頃には確認済みのように読めます。
  結論だけを見るレビュアーには、一貫して見えます。生の証拠を誰かに確認させます。ただし、それを作った agent には確認させません。

---

## 実行

[`src/`](src/) は 15 を引き継いだうえで、次を追加します。

- [`mailbox.py`](src/mailbox.py): ロック付きの名前付き inbox、メッセージの織り込み、`serve_mailbox` の loop、timeout 付きで既定は拒否となる引き上げ、そしてチーム用の tool 群。
- [`test.py`](src/test.py): 宛先指定、ブロードキャスト、同時送信、織り込み、引き上げ (インライン、非同期、timeout での拒否)、mailbox の loop、チーム用の tool を確認します。
- [`demo.py`](src/demo.py): lead が 1 ステップ進め (`TeamCreate`、`SpawnTeammate`、`SendMessage`)、各メンバーは自分の inbox を取りに行き、ゲートのかかった shell タスクを実行し、結果を報告します。

loop と subagent の経路は変わりません。coordination は、メンバーを spawn し、inbox を drain し、承認者を渡すことで turn を包みます。

```bash
python sections/16-coordination/src/test.py         # offline checks, no key
uv run python sections/16-coordination/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code tools and inboxes](https://github.com/yasasbanukaofficial/claude-code):
  `tools/SendMessageTool/`, `tools/TeamCreateTool/`, `utils/mailbox.ts`, `utils/teammateMailbox.ts`.
- [Claude Code teammates](https://github.com/yasasbanukaofficial/claude-code):
  `tasks/InProcessTeammateTask/`, `tasks/RemoteAgentTask/`, `remote/remotePermissionBridge.ts`, `memdir/teamMemPaths.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent): `tools/delegate_tool.py`, `tools/async_delegation.py`, `tools/clarify_gateway.py`, `tools/interrupt.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `docs/subsystems/workflow.md`, `docs/subsystems/subagent.md`, `docs/subsystems/core.md`,
  `packages/workflow/workflow-worker-thread/README.md`, `packages/subagent/tool-subagent-report/README.md`.
- [learn-claude-code · s15_agent_teams](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter10.md` (多 Agent 协作), Chinese original canonical.
  Context sharing, topology taxonomy, filesystem regions, handoff packets. The role-transfer demo is the author's own experiment.
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?* ([arXiv:2503.13657](https://arxiv.org/abs/2503.13657)): the MAST taxonomy and the Byzantine framing.
- Tran, Kiela, *Single-Agent LLMs Outperform Multi-Agent Systems Under Equal Thinking Token Budgets* ([arXiv:2604.02460](https://arxiv.org/abs/2604.02460)).
- Erdogan et al., *Plan-and-Act* ([arXiv:2503.09572](https://arxiv.org/abs/2503.09572)): planner quality bounds the run.
- Anthropic, [*How we built our multi-agent research system*](https://www.anthropic.com/engineering/multi-agent-research-system): token cost of a research team.
- [MetaGPT](https://arxiv.org/abs/2308.00352), [AutoGen](https://arxiv.org/abs/2308.08155), [OpenAI Swarm](https://github.com/openai/swarm): decentralized routing and handoff caps.
