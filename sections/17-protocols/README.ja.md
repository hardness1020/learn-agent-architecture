# 17 · Protocols

[English](README.md) · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · **日本語** · [한국어](README.ko.md)

> メッセージに契約を付ける: 行動する前に承認し、停止する前に確認します。

調整 (セクション 16) はエージェントにチャネルを与えますが、チャネルはテキストを移動するだけです。
テキストだけではルールがありません。応答から要求を伝えるものはなく、一方が行動する前に応答を待たせるものはありません。

プロトコルとは、チャネル上の合意されたルールであり、リクエストとその応答がどのように形成されるか、および応答が応答するリクエストとどのように照合されるかというものです。

これが最も必要な状況は 2 つあります。編集中にチームメイトを殺害したリードは、書きかけのファイルと開いたタスクの記録を残します。

質問もせずに危険なリファクタリングを実行するチームメイトは、最初に行動し、後で報告します。

どちらも同じことを必要とします。つまり、一方が要求し、もう一方が応答し、ID がそれらを結び付けます。

プロトコルは次のことを行う必要があります。

1. リクエストとその応答に型付きの形状を与えます。
2. 各応答を応答するリクエストに関連付けます。
3. 作業を開始する前に、危険な計画をゲートします。
4. 実行中の作業を失わずにエージェントを停止します。
5. 1 人のワーカーが勝ったらファンアウト全体を停止し、そのレースを 1 回だけ決着させます。
6. 信頼境界を越えて、チーム外のエージェントに連絡します。

この層がなければ、調整は構造化されていないチャットになります。何もゲートされておらず、何もきれいに停止せず、応答とその応答が一致することはありません。

---

## メカニズム

![機構図](assets/17-protocols.png)

すべての交換は、1 つの `requestId` を共有する型指定されたリクエストと型指定された応答です。

送信者はリクエストを保留中として記録し、そのタイプに応じて返信をルーティングし、一致するリクエストを解決します。

単なる 2 つのメッセージではなく、3 つのルールがプロトコルになります。

- **型付きバリアント** 各メッセージは、`type` フィールド上の 1 つのバリアントです。ハンドラーはその型に応じてディスパッチされるため、応答が無関係なリクエストと間違われることはありません。
- **相関 ID.** `requestId` は、要求が送信され、応答でエコーされるときに設定されます。送信者は、応答によってどの保留中のリクエストが解決されるかを知っています。
- **小さなステート マシン。** リクエストは `pending` に続いて、`approved` または `rejected` に進みます。すでに解決された ID に対する応答は無視されるため、重複しても無害です。

シャットダウン フローと計画フローは、反対方向の同じ交換です。
シャットダウンでは、リードがリクエストし、チームメイトが確認します。計画の承認では、チームメイトがリクエストし、リーダーが確認します。

承認には作業を実行するための許可モードも含まれるため、評決とモードは連動します (セクション 3)。

### 新機能: プロトコル トラッカー

`protocols.py` は、セクション 16 チャネル上のエージェントごとに 1 つの `Protocol` です。リクエストは相関 ID を作成し、それ自体を保留状態に記録します。応答ではその ID がエコーバックされます。

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

- `request` は各 ID `me-N` に番号を付けるため、ID は送信者ごとに一意であり、エージェント間で衝突することはありません。
- `reply` はリクエストの `request_id` を再利用します。このエコーこそがすべてのトリックです。これは、送信者が後で返信を返信内容と照合する方法です。

小さなテーブル名は、どの応答の種類が各リクエストに応答するかを示し、それぞれの判定は次のことを意味します。

```python
_REPLIES = {                                           # src/protocols.py
    "shutdown_request": {"shutdown_approved": APPROVED, "shutdown_rejected": REJECTED},
    "plan_approval_request": {"plan_approval_response": None},   # None: the verdict rides an `approved` field
}
```

`resolve` は、そのテーブルを読み取り、不一致の応答を拒否し、判定を 1 回だけ記録します。

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

- `resolve` はべき等です。重複または迷走した応答は `state != PENDING` または不明 ID ガードにヒットし、`None` を返します。
- `verdicts` ルックアップは型混同ガードです。その型はシャットダウン行にないため、`plan_approval_response` は `shutdown_request` を解決できません。
- シャットダウンは、その判定を 2 種類の応答に分割します。計画の承認にはブール値を保持する 1 種類が使用されます。どちらも同じ `pending` から `approved` または `rejected` 状態になります。
- `protocol_tools` は、ハンドシェイクの開始をツールとして公開します (`ExitPlanMode`、`ApprovePlan`、`StopTeammate`)。
- シャットダウンの確認はツールではありません。チームメイトの `run_teammate` ループは自動的に応答します (ハーネス駆動受信)。

### 新機能: チームメイト ループ

`run_teammate` は、セクション 16 の `serve_mailbox` にシャットダウン ハンドシェイクが組み込まれたものです。生成されたチームメイトは、デーモン スレッドで死ぬのではなく、リクエストで停止するようになりました。

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

- シャットダウンはチャットの前にチェックされるため、ピア トラフィックが停止することはありません。
- 開始はモデル駆動型です (リードの `StopTeammate`)。受信はハーネス主導で (ループが確認します)、リファレンスの分割と一致します。
- ループは `"shutdown"` を返すため、生成中の runtime (セクション 13) はクリーン ストップを報告します。
- セクション 18 では、さらに分岐を 1 つ追加します。受信箱が空のときに共有ボードからタスクを要求します。

### 統合方法

デモでは 1 つのメイン エージェントが実行されます。リードはチームメイトを生成し、委任し、1 ターンでチームメイトを停止します。チームメイトは独自のスレッドで確認しています。

```python
def spawn_worker(name, team, model):                   # src/demo.py, module level
    ...                                                 # build the teammate's tools
    return run_teammate(team, name, "lead", work)       # serve_mailbox plus the shutdown handshake

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
state = next(filter(None, (lead_proto.resolve(m) for m in team.drain("lead")   # -> approved
                           if isinstance(m["content"], dict))), None)
```

- `demo()` は、先頭の `run_turn` を 1 つ実行します。 `SpawnTeammate`、`SendMessage`、次に `StopTeammate` を呼び出します。
- `StopTeammate` は `shutdown_request` を送信します。チームメイトの `run_teammate` がそれを確認して返します。停止は握手であり、殺害ではありません。
- リードは、エコーされた `shutdown_approved` を `approved` に解決します。メインプロセスは待機するだけです。
- 計画承認フローは対称逆 (`ExitPlanMode`、次に `ApprovePlan`) であり、同じツールによって駆動され、test.py で証明されています。
- ループは変わりません。プロトコルは、チャネル上でリクエストを形成し、応答を解決することによってターンをラップします。

### さらに読む

これは `src/` にはありません。これは ai-agent-book および A2A 仕様に基づいており、表内のシステムについては確認されていません。

**ファン アウト全体を停止する。** ファン アウトでは、1 つの問題に複数の作業者が派遣され、必要な答えは 1 つだけです。
最初に成功したワーカーが報告を返し、リードは他のすべてのワーカーに停止を送信します。
このデモで止められるのはチームメイト 1 人だけですが、ここで新たな動きは何もありません。各停留所は同じリクエストと確認であり、
したがって、負けたワーカーはファイルを終了し、タスク レコードを閉じます。多くの人に送られたシャットダウンフローです。

**同時に 2 人の勝者。** 2 人の作業者が同時に成功する可能性があります。その後、両方とも先頭としてカウントされ、リードが 2 ラウンドのストップを送信し、2 つの結果が記録されます。
ロックはそれを解決します。最初に到着したワーカーがロックを受け取り、誰が勝ったかを書き留めてロックを解除します。
2 番目は次にロックを取得し、勝者がすでに書き込まれていることを確認し、誰も止めずに戻ります。誰が先に到着しても、レースは一度決着します。

**確認が来ない場合** 確認を待っている停止は応答されない可能性があります。長いツール呼び出し中のワーカーは受信トレイを読んでいません。
したがって、停留所は 2 層になっています。リードは要求し、期限まで確認を待ち、その後、まだ実行中のものを強制終了します。
キルは先手ではなく後退です。リーダーが最初に質問するため、時間があればいつでもクリーンアップが実行されます。

**情報源は 1 つであり、調査ではありません。** 層とロックはどちらも、複数のシステムの比較からではなく、この本の著者自身による 1 つの実験から得られたものです。

**あなたが所有していないエージェントとの会話。** 上記のすべては、1 つのチーム、1 つのプロセス、1 人の所有者を前提としています。
チャネルは共有され、名簿は生成時に認識され、すべてのエージェントはネットワーク上の ID を信頼します。
そのどれもが組織の境界を越えて生き残ることはできません。 `request_id` をスタンプするための共有受信トレイはありません。相手側の名簿は表示されません。そのツールリストは信頼できません。
A2A はその場合のプロトコルです。要求と応答のコアを維持し、3 つの部分を追加します。

- **エージェント カードの検出。** 各エージェントは、名前、skills、エンドポイント、認証方法などの既知の URL でドキュメントを公開します。
  発信者は最初にカードを読み取り、次に何を送信するかを決定します。チーム内では、名簿が作成時間に到着します。境界を越えると、呼び出し元はそれをフェッチする必要があります。
- **タスクのライフサイクル。** リモート呼び出しは、ID と状態 (`submitted`、`working`、`input-required`、`completed`、`failed`) を持つタスクです。発信者はその ID でポーリングまたはサブスクライブします。
  `input-required` は、このセクションに名前のない状態です。リモート エージェントは一時停止して詳細情報を要求しますが、タスクは待機中も存続します。
- **不透明なアーティファクト** 結果は、ファイル、テキスト、構造化パーツなどのアーティファクトとして返されます。リモート エージェントの軌跡は決して戻りません。
  呼び出し側は、作業がどのように行われたかを確認できません。結果だけが交差します。

**リクエスト状態とタスク状態** 2 つの設計は異なるものを追跡します。このセクションでは 1 つのリクエストを追跡します。リクエストは `pending`、次に `approved` または `rejected` となります。
A2A は、`submitted`、`working`、`input-required`、`completed`、`failed` の 1 つのタスクを追跡します。
違いはレコードの存続期間です。リクエスト レコードは、それを作成したエクスチェンジで終了します。
タスク ID は、応答が到着した後、詳細情報を得るために一時停止した後、接続が切断されて戻った後など、後で解決されます。
境界を越えた呼び出し元は両方を保持します。リクエストには、この 1 つのメッセージが受け入れられたかどうかが示されています。タスクの状態は、ジョブ全体の状況を示します。

---

## システムごと

リクエストを形成し、計画を制御し、エージェントを適切に停止するように設計する方法。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **長所** |すべての停止が確認され、すべての危険な計画がゲートされます。 |パブリック プロトコルを使用するクライアントまたはサーバーは相互運用します。 |
| **短所** |各ハンドシェイクにはラウンドトリップとプロトコル状態がかかります。 |出力はコミットされた場合にのみ到達するため、ライブの進行状況は非表示のままになります。 |
| **理由** |編集途中で強制終了すると、書き込み途中のファイルが残ります。危険な計画にはまず承認が必要です。 |もう一方のプロセスはあなたが所有できない可能性があるため、公的契約を使用します。 |
| **方法: メッセージの形状** | `type` フィールド上の 1 つの型付き共用体 (応答ごとに `request_id`)。 | JSON-RPC セッション ID をキーとするメソッド。セッションごとに 1 つのプロンプトが実行中です。 |
| **方法: 計画の承認** |チームメイトは待っています。リードの返信には、評決、フィードバック、モードが含まれます。 |計画は人間に及ぶ。拒否は、フィードバック付きの失敗した通話として返されます。 |
| **方法: シャットダウン** |リードがリクエストし、チームメイトが確認してからキルが実行されます。 |キャンセルして入力を終了し、シグナルを送ってから強制終了します。すべての階層には時間制限があります。 |

---

## 障害モード

- **ハンドシェイクの代わりにハードキル。** チームメイトのスレッドを殺すと飛行作業が低下し、そのタスク記録が孤立します。リクエストを使用して、タスク `notified` をマークするフローを確認します。
- **孤立したリクエスト。** 応答が到着しないと、リクエスト `pending` が永久に残るため、送信者はブロックします。スタックしたリクエストを明らかにするタイムアウトまたはアイドル チェックを追加します。
- **タイプの混乱。** ID のみで応答を照合すると、シャットダウン応答でプラン リクエストを解決できます。応答バリアントが記録されたリクエスト タイプと一致することを確認します。
- **強制を伴わない承認。** 承認された計画には、実行をゲートする許可レイヤーが必要です (セクション 3)。応答に `permissionMode` を含めます。
- **返信の重複。** 返信を再試行すると、すでに解決された状態が反転する可能性があります。保留されていない ID への返信は何も行われないものとして扱います。
- **ロックを使用せずにファンアウトを停止します。** 2 人のワーカーが同じ瞬間に終了するため、両方とも最初としてカウントされます。その後、リードは2ラウンドのストップを送り、2人の勝者を記録します。
  誰が勝ったかを書き留める前にロックを解除してください。後の勝者は、すでに名前が存在しているのを見て、誰も止めません。
- **期限なしで確認を待機しています。** 長いツール呼び出しで忙しいワーカーは、リクエストを決して読みません。確認は決して到着せず、リードは永遠に待ちます。
  待機に期限を設けて、それが過ぎたら殺す。尋ねることが最初の行動であり続けます。もはやそれは唯一のものではありません。
- **リモート タスクは 1 つの応答として扱われます。** 自分が所有していないエージェントは、一時停止して詳細を尋ねることができます。これはタスクの状態であり、応答ではありません。
  タスク ID とその状態を追跡します。一時停止されたジョブは、そのジョブを開始した交換が終了した後も引き続きアドレス指定可能です。

---

## 実行可能

[`src/`](src/) は 16 を繰り上げて次を追加します。

- [`protocols.py`](src/protocols.py): リクエスト トラッカー (型付きバリアント、相関 ID、ステート マシン)、ハンドシェイク ツール、および `run_teammate` ループ。
- [`test.py`](src/test.py): シャットダウンと計画のフロー、ガード、ツール主導のハンドシェイク、およびハンドシェイクによって停止された自走チームメイトをチェックします。
- [`demo.py`](src/demo.py): 1 リード ターンでチームメイトが生成され、委任され、StopTeammate でチームメイトを停止します。チームメイトは独自のスレッドで確認します。

ループと subagent パスは変更されません。プロトコルは、チャネル上でリクエストを形成し、応答を解決することによってターンをラップします。

```bash
python sections/17-protocols/src/test.py         # offline checks, no key
uv run python sections/17-protocols/src/demo.py  # live demo, needs a key
```

---

## ソース

- [Claude Code プロトコル形状](https://github.com/yasasbanukaofficial/claude-code): `tools/SendMessageTool/SendMessageTool.ts`、`utils/teammateMailbox.ts`。
- [Claude Code 計画と停止](https://github.com/yasasbanukaofficial/claude-code):
  `tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`、`tasks/stopTask.ts`、`coordinator/coordinatorMode.ts`。
- [deepseek-harness ソース](https://github.com/deepseek-ai/deepseek-harness) `dsh-v0.1.0-rc.7`:
  `packages/acp/acp/README.md`、`packages/subagent/subagent-acp/README.md`、`docs/subsystems/session.md`、
  `docs/subsystems/plan.md`、`docs/subsystems/approval.md`。
- [learn-claude-code · s16_team_protocols](https://github.com/shareAI-lab/learn-claude-code): セクション フレーム。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter10.md` (多エージェント协作)、中国語オリジナルの正規版。
  クリーンアップとACKを行うストップ、フォールバック層としてのキル、そしてロックによる最初の成功でファン全体を停止して、レースが一度決着するようにします。
  どちらも、本の著者自身の実験、単一の情報源に基づいています。
- [A2A プロトコル](https://github.com/a2aproject/A2A) (Linux Foundation): エージェント カードの検出、タスクのライフサイクルの状態
  (`submitted`、`working`、`input-required`、`completed`、`failed`)、および信頼境界を越えた不透明なアーティファクト交換。
