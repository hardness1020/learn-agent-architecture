# 17 · Protocols

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> メッセージに契約を与えます。動く前に承認を取り、止まる前に確認を返します。

coordination (section 16) は agent に経路を与えますが、経路が運ぶのはテキストだけです。
テキストだけでは規則がありません。要求と返信を区別するものがなく、片方が動く前に答えを待つようにさせるものもありません。

protocol とは、その経路の上に載る取り決めです。要求と返信をどんな形にするか、そして返信をどの要求に対応付けるか、を定めます。

これがとくに要る場面は 2 つです。編集の途中でメンバーを kill する lead は、書きかけのファイルと開いたままの task のレコードを残します。

危険なリファクタリングを尋ねずに実行するメンバーは、先に動いてから報告することになります。

どちらにも必要なのは同じものです。片方が要求し、もう片方が返信し、id が両者を結び付けます。

protocol に必要な機能は次のとおりです。

1. 要求と返信に型付きの形を与える。
2. 各返信を、それが答えている要求へ対応付ける。
3. 危険な計画を、作業が始まる前にゲートで止める。
4. 進行中の作業を失わずに agent を停止する。
5. 1 体の worker が勝った時点で fan out 全体を止め、その競合をちょうど一度だけ決着させる。
6. 信頼境界をまたいで、チーム外の agent へ到達する。

この層がないと、coordination は構造のない雑談です。何もゲートされず、何もきれいに止まらず、返信を答えの対象へ対応付けられません。

---

## 仕組み

![Mechanism diagram](assets/17-protocols.png)

やり取りはすべて、1 つの `requestId` を共有する型付きの要求と型付きの応答です。

送信側は要求を pending として記録し、返信をその型で振り分け、対応する要求を解決します。

これを単なる 2 通のメッセージではなく protocol にしているのは、3 つの規則です。

- **型付きのバリアント。** 各メッセージは `type` フィールド上の 1 バリアントです。ハンドラが型で dispatch するので、返信が無関係な要求と取り違えられることはありません。
- **相関 id。** `requestId` は要求を送るときに設定され、返信でそのまま返ってきます。送信側は、その返信がどの pending な要求を解決するのかが分かります。
- **小さな状態機械。** 要求は `pending` から `approved` または `rejected` へ進みます。すでに解決済みの id への返信は無視されるので、重複しても害はありません。

shutdown の流れと計画の流れは、同じやり取りを向きだけ逆にしたものです。
shutdown では lead が要求し、メンバーが確認を返します。計画の承認ではメンバーが要求し、lead が確認を返します。

承認には、その作業が動く permission モードを載せることもできるので、判定とモードが一緒に運ばれます (section 3)。

### 新規: protocol のトラッカー

`protocols.py` は、section 16 の経路の上に置かれる agent ごとの `Protocol` 1 つです。要求は相関 id を発行して自分を pending として記録し、返信はその id をそのまま返します。

```python
def request(self, to, kind, **fields):                 # src/protocols.py
    self._n += 1
    rid = f"{self.me}-{self._n}"                        # per-sender id: unique, deterministic
    self.pending[rid] = {"kind": kind, "state": PENDING}
    self.team.send(self.me, to, {"type": kind, "request_id": rid, **fields})
    return rid

def reply(self, msg, kind, **fields):                  # echo the id back, do not mint a new one
    req = msg["content"]
    self.team.send(self.me, msg["from"], {"type": kind, "request_id": req["request_id"], **fields})
```

- `request` は id に `me-N` と番号を振るので、id は送信者ごとに一意で、agent をまたいで衝突しません。
- `reply` は要求の `request_id` を使い回します。仕掛けはこの反射だけで、送信側があとで返信を答えの対象へ対応付けられるのはこのおかげです。

小さなテーブルが、どの返信の種類がどの要求に答えられるか、そしてそれぞれが意味する判定を定めます。

```python
_REPLIES = {                                           # src/protocols.py
    "shutdown_request": {"shutdown_approved": APPROVED, "shutdown_rejected": REJECTED},
    "plan_approval_request": {"plan_approval_response": None},   # None: the verdict rides an `approved` field
}
```

`resolve` はそのテーブルを読み、食い違う返信を弾き、判定をちょうど一度だけ記録します。

```python
def resolve(self, msg):                                # src/protocols.py
    reply = msg["content"]
    req = self.pending.get(reply.get("request_id"))
    if not req or req["state"] != PENDING:             # unknown id or already resolved
        return None
    verdicts = _REPLIES[req["kind"]]
    if reply.get("type") not in verdicts:              # type-confusion guard
        return None
    state = verdicts[reply["type"]]
    if state is None:                                  # single-response flow carries the bool
        state = APPROVED if reply.get("approved") else REJECTED
    req["state"] = state
    return state
```

- `resolve` は冪等です。重複した返信や迷子の返信は `state != PENDING` か未知の id のガードに当たり、`None` を返します。
- `verdicts` の引きが型取り違えのガードです。`plan_approval_response` が `shutdown_request` を解決することはできません。その型が shutdown の行にないからです。
- shutdown は判定を 2 つの返信の種類に分けます。計画の承認は 1 つの種類に bool を載せます。どちらも同じ `pending` から `approved` または `rejected` への状態へ着地します。
- `protocol_tools` は、ハンドシェイクの開始側を tool として公開します (`ExitPlanMode`、`ApprovePlan`、`StopTeammate`)。
- shutdown の確認は tool ではありません。メンバーの `run_teammate` の loop が自動で返信します (harness 主導の受信)。

### 新規: メンバーの loop

`run_teammate` は、section 16 の `serve_mailbox` に shutdown のハンドシェイクを織り込んだものです。spawn されたメンバーは、デーモン thread とともに死ぬのではなく、要求を受けて停止するようになります。

```python
def run_teammate(team, me, lead, work, *, poll=0.05, max_idle_polls=None):   # src/protocols.py
    proto = Protocol(team, me)
    while True:
        inbox = team.drain(me)
        shutdown = next((m for m in inbox if _is_shutdown(m)), None)
        if shutdown is not None:
            proto.reply(shutdown, "shutdown_approved")     # confirm, then stop
            return "shutdown"
        chat = [m for m in inbox if isinstance(m["content"], str)]
        if chat:
            work(_fold(chat)); continue                    # section 16: fold and run
        time.sleep(poll)                                   # empty: poll again
```

- shutdown はチャットより先に確認するので、ピアからの通信量で停止がリソース不足に陥ることはありません。
- 開始側はモデル主導 (lead の `StopTeammate`)、受信側は harness 主導 (loop が確認を返す) で、参照実装の分け方と一致します。
- loop は `"shutdown"` を返すので、spawn したランタイム (section 13) がきれいな停止として報告できます。
- section 18 は分岐をもう 1 つ足します。inbox が空のとき、共有ボードから task を確保する分岐です。

### 組み込み方

デモが動かすメインの agent は 1 体です。lead は 1 つの turn の中でメンバーを spawn し、仕事を任せ、停止させます。メンバーは自分の thread 上で確認を返します。

```python
def spawn_worker(name, team, model):                   # src/demo.py, module level
    ...                                                 # build the teammate's tools
    return run_teammate(team, name, "lead", work)       # serve_mailbox plus the shutdown handshake

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
state = next(filter(None, (lead_proto.resolve(m) for m in team.drain("lead")   # -> approved
                           if isinstance(m["content"], dict))), None)
```

- `demo()` が走らせる `run_turn` は 1 つ、lead のものだけです。lead は `SpawnTeammate`、`SendMessage`、そして `StopTeammate` を呼びます。
- `StopTeammate` は `shutdown_request` を送ります。メンバーの `run_teammate` がそれを確認して返ります。停止は kill ではなくハンドシェイクです。
- lead は返ってきた `shutdown_approved` を `approved` として解決します。メインプロセスは待つだけです。
- 計画の承認の流れは、これを左右対称に反転させたものです (`ExitPlanMode` のあとに `ApprovePlan`)。同じ tool 群が動かし、test.py で確認しています。
- loop は変わりません。protocol は、経路の上で要求を形づくり、返信を解決することで turn を包みます。

### さらに読む

ここから先は `src/` にはありません。ai-agent-book と A2A の仕様に基づく内容で、表に挙げたシステムでの裏付けは取れていません。

**fan out 全体を止める。** fan out は 1 つの問題に複数の worker を差し向けますが、必要な答えは 1 つだけです。
最初に成功した worker が報告し、lead はそれから他のすべての worker へ停止を送ります。
デモが止めるメンバーは常に 1 体だけですが、通信路上を流れるものは何も新しくありません。どの停止も同じ要求と確認なので、
負けた worker も自分のファイルを書き終え、自分の task レコードを閉じられます。shutdown の流れを、多数へ送るだけです。

**同時に 2 体が勝つ。** 2 体の worker がまったく同じ瞬間に成功することがあります。すると両方が 1 着扱いになり、lead は停止を 2 巡送り、結果が 2 件記録されます。
これはロックで直ります。先に到着した worker がロックを取り、誰が勝ったかを書き込み、ロックを解放します。
2 体目は次にロックを取り、すでに勝者が書かれているのを見て、誰も止めずに戻ります。どちらが先に着いても、競合の決着は一度きりです。

**確認が来ないとき。** 確認を待つ停止は、答えが返らないことがあります。長い tool 呼び出しの中にいる worker は、自分の inbox を読んでいません。
そこで停止は 2 段構えにします。lead はまず尋ね、期限まで確認を待ち、それでも動いているものを kill します。
kill はフォールバックであり、最初の一手ではありません。lead はまず尋ねるので、時間がある限り後片付けは走ります。

**単一ソースであって、比較調査ではない。** この 2 段構えもロックも、本の著者自身による 1 つの実験から来ており、複数システムの比較から来たものではありません。

**自分の持ち物ではない agent と話す。** ここまでの話は、1 チーム、1 プロセス、1 所有者を前提にしています。
経路は共有され、名簿は spawn 時点で分かっており、どの agent も通信路上の id を信頼します。
組織の境界をまたぐと、その前提はどれも成り立ちません。`request_id` を刻む共有の inbox はありません。相手側の名簿は見えません。相手の tool 一覧も信頼できません。
その場合の protocol が A2A です。要求と返信という中核を保ったまま、3 つの部品を足します。

- **Agent Card による発見。** 各 agent は既知の URL にドキュメントを公開します。名前、skill、エンドポイント、認証方法です。
  呼び出し側はまずカードを読み、それから何を送るか決めます。チームの内側では名簿が spawn 時点で届きます。境界をまたぐ場合は、呼び出し側が取りに行く必要があります。
- **task のライフサイクル。** リモート呼び出しは、id と状態を持つ task です。状態は `submitted`、`working`、`input-required`、`completed`、`failed` です。呼び出し側はその id をポーリングするか購読します。
  `input-required` は、このセクションが名前を持たない状態です。リモートの agent は一時停止して追加の情報を求め、待っている間も task は生き続けます。
- **不透明な artifact。** 結果は artifact として返ります。ファイル、テキスト、構造化されたパートです。リモートの agent の trajectory が返ることはありません。
  呼び出し側には、作業がどう進んだかは見えません。境界を越えるのは結果だけです。

**要求の状態と task の状態。** この 2 つの設計が追うものは別です。このセクションが追うのは要求 1 つで、`pending` から `approved` または `rejected` へ進みます。
A2A が追うのは task 1 つで、`submitted`、`working`、`input-required`、`completed`、`failed` です。
違うのはレコードの寿命です。要求のレコードは、それを生んだやり取りとともに終わります。
task の id はそのあとも解決できます。返信が届いたあとも、追加情報を待つ一時停止のあとも、接続が切れて戻ったあともです。
境界をまたぐ呼び出し側は、両方を持ちます。要求の状態は、このメッセージ 1 通が受理されたかどうかを示します。task の状態は、仕事全体がどこまで進んだかを示します。

---

## システム別

ある設計が、要求をどう形づくり、計画をどうゲートし、agent をどうきれいに止めるかを示します。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **利点** | どの停止も確認を伴い、危険な計画はすべてゲートされます。 | 公開された protocol を話すクライアントやサーバーであれば、どれとでも相互運用できます。 |
| **欠点** | ハンドシェイクごとに往復と protocol の状態のコストがかかります。 | 出力が届くのはコミット時だけなので、進行中の様子は見えません。 |
| **理由** | 編集の途中での kill は書きかけのファイルを残します。危険な計画には先に承認が要ります。 | 相手は自分の持ち物ではないかもしれないプロセスなので、公開された契約を使います。 |
| **方法: メッセージの形** | `type` フィールド上の型付き union 1 つで、返信ごとに `request_id` を持ちます。 | session id をキーとする JSON-RPC のメソッドです。1 session につき同時に 1 prompt です。 |
| **方法: 計画の承認** | メンバーが待ちます。lead の返信が判定、フィードバック、モードを運びます。 | 計画は人間へ回ります。却下は、フィードバック付きの失敗した呼び出しとして返ります。 |
| **方法: shutdown** | lead が要求し、メンバーが確認し、そのあとで kill が走ります。 | キャンセル、入力の終了、シグナル、そして kill。どの段にも時間の上限があります。 |

---

## 失敗モード

- **ハンドシェイクの代わりに強制終了。** メンバーの thread を kill すると進行中の作業が落ち、task のレコードが孤児になります。task を `notified` にする、要求と確認の流れを使う。
- **孤児化した要求。** 返信が届かないと要求は永久に `pending` のままになり、送信側がブロックします。timeout かアイドル検査を足し、詰まった要求を表に出す。
- **型の取り違え。** id だけで返信を対応付けると、shutdown の返信が計画の要求を解決できてしまいます。返信のバリアントが、記録された要求の型と一致するか検査する。
- **強制のない承認。** 承認された計画でも、実行をゲートするには permission 層が必要です (section 3)。応答に `permissionMode` を載せる。
- **重複した返信。** 再送された返信が、解決済みの状態を覆すことがあります。pending でない id への返信はすべて何もしない扱いにする。
- **ロックなしで fan out を停止。** 2 体の worker が同じ瞬間に終わり、両方が 1 着扱いになります。すると lead は停止を 2 巡送り、勝者を 2 件記録します。
  誰が勝ったかを書き込む前にロックを取る。あとから勝った側は、すでに名前があるのを見て、誰も止めません。
- **期限なしで確認を待機。** 長い tool 呼び出しで忙しい worker は要求を読みません。確認は届かず、lead は永久に待ちます。
  待ちに期限を置き、過ぎたら kill する。まず尋ねることは変わりません。それが唯一の手ではなくなるだけです。
- **リモートの task を返信 1 通として扱う。** 自分の持ち物ではない agent は、一時停止して追加の情報を求めることがあります。それは task の状態であって、返信ではありません。
  task の id とその状態を追う。そうすれば、一時停止中の仕事も、それを始めたやり取りが終わったあとで宛先として指定できます。

---

## 実行

[`src/`](src/) は 16 を引き継いだうえで、次を追加します。

- [`protocols.py`](src/protocols.py): 要求のトラッカー (型付きバリアント、相関 id、状態機械)、ハンドシェイク用の tool、そして `run_teammate` の loop。
- [`test.py`](src/test.py): shutdown と計画の流れ、各ガード、tool 主導のハンドシェイク、そしてハンドシェイクで停止する自走メンバーを確認します。
- [`demo.py`](src/demo.py): lead の 1 turn がメンバーを spawn し、仕事を任せ、StopTeammate で停止させます。メンバーは自分の thread 上で確認を返します。

loop と subagent の経路は変わりません。protocol は、経路の上で要求を形づくり、返信を解決することで turn を包みます。

```bash
python sections/17-protocols/src/test.py         # offline checks, no key
uv run python sections/17-protocols/src/demo.py  # live demo, needs a key
```

---

## 出典

- [Claude Code protocol shape](https://github.com/yasasbanukaofficial/claude-code): `tools/SendMessageTool/SendMessageTool.ts`, `utils/teammateMailbox.ts`.
- [Claude Code plan and stop](https://github.com/yasasbanukaofficial/claude-code):
  `tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`, `tasks/stopTask.ts`, `coordinator/coordinatorMode.ts`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/acp/acp/README.md`, `packages/subagent/subagent-acp/README.md`, `docs/subsystems/session.md`,
  `docs/subsystems/plan.md`, `docs/subsystems/approval.md`.
- [learn-claude-code · s16_team_protocols](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter10.md` (多 Agent 协作), Chinese original canonical.
  A stop that cleans up and acks, a kill as the fallback tier, and stopping a whole fan out on first success with a lock so the race settles once.
  Both rest on the book author's own experiment, a single source.
- [A2A protocol](https://github.com/a2aproject/A2A) (Linux Foundation): Agent Card discovery, the task lifecycle states
  (`submitted`, `working`, `input-required`, `completed`, `failed`), and opaque artifact exchange across a trust boundary.
