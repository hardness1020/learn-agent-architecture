# 17 · Protocols

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> 給訊息一份合約：先核准再行動，先確認再停止。

第 16 章的 coordination 提供了溝通管道，但管道只能傳遞文字。若沒有額外規則，系統無法分辨請求與回覆，也不能保證 agent 會先等待核准再行動。

protocol 是建立在通訊管道上的共同約定，用來定義請求與回覆的格式，以及每則回覆要如何對應到原始請求。

這套規則在兩種情況特別重要。第一種是 lead 強制停止正在編輯的隊友，留下只寫到一半的檔案和未關閉的 task。第二種是隊友沒有先取得核准，就直接執行高風險重構，等做完才回報。

兩種情況需要的核心機制相同：一方送出請求，另一方明確回覆，再用同一個 id 把兩者關聯起來。

protocol 必須：

1. 為請求與回覆定義固定格式。
2. 將每則回覆對應到正確的原始請求。
3. 在高風險工作開始前先取得核准。
4. 停止 agent 時保留尚未完成的工作。
5. 多個 worker 競速時，第一個完成者能結束整批工作，而且只結算一次。
6. 與團隊外部的 agent 溝通時，仍能跨越信任邊界安全運作。

少了 protocol，coordination 就只是 agent 彼此傳文字。高風險動作沒有核准機制，停止時無法完整收尾，收到回覆後也不知道它對應哪個請求。

---

## 核心機制

![機制圖](assets/17-protocols.png)

每一次來回都是一則帶型別的請求，配一則帶型別的回應，兩者共用同一個 `requestId`。

sender 把請求記為 pending，依型別路由回覆，並解析出相符的請求。

有三條規則讓它成為一個 protocol，而不只是兩則訊息：

- **Typed variants：**每則訊息是 `type` 欄位上的一個 variant。handler 依型別 dispatch，所以回覆絕不會被誤認為某個不相干的請求。
- **Correlation id：**`requestId` 在請求送出時設定，並在回覆裡回傳。sender 就知道一則回覆解析的是哪一筆 pending 請求。
- **A small state machine：**一筆請求從 `pending` 走到 `approved` 或 `rejected`。一個 id 有了結果之後，再收到的回覆都會被忽略，所以同一則回覆重複送也沒關係。

shutdown 與 plan 這兩個流程一樣，只是方向相反：shutdown 是 lead 請求、隊友確認；plan approval 是隊友請求、lead 確認。

審核的回覆裡也可以附上這件工作要用哪種權限模式（第 3 章），核准和模式一次送到。

### 本章新增：protocol 追蹤器

`protocols.py` 是每個 agent 在第 16 章管道之上的一個 `Protocol`。一筆請求鑄造一個 correlation id 並把自己記為 pending；回覆把那個 id 回傳：

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

- `request` 把每個 id 編號為 `me-N`，所以 id 對每個 sender 都唯一，且跨 agent 絕不衝突。
- `reply` 重用請求的 `request_id`。那個回傳就是整個訣竅所在：sender 之後就是靠它把回覆對應到它所回答的內容。

一張小表指明哪些回覆種類可以回答每種請求，以及各自代表的裁決：

```python
_REPLIES = {                                           # src/protocols.py
    "shutdown_request": {"shutdown_approved": APPROVED, "shutdown_rejected": REJECTED},
    "plan_approval_request": {"plan_approval_response": None},   # None: the verdict rides an `approved` field
}
```

`resolve` 讀這張表，用來拒絕不相符的回覆，而且裁決只記錄一次：

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

- `resolve` 是 idempotent 的：重複或不相干的回覆會撞上 `state != PENDING` 或未知 id 的守衛，並回傳 `None`。
- `verdicts` 查表就是 type-confusion 守衛：一則 `plan_approval_response` 無法解析一筆 `shutdown_request`，因為那個型別不在 shutdown 那一列裡。
- shutdown 把它的裁決拆到兩個回覆種類；plan approval 用一個攜帶 bool 的種類。兩者都落到同一個從 `pending` 到 `approved` 或 `rejected` 的狀態。
- `protocol_tools` 把 handshake 的發起作為工具暴露出來（`ExitPlanMode`、`ApprovePlan`、`StopTeammate`）。
- 確認一個 shutdown 不是一個工具；隊友的 `run_teammate` loop 會自動回覆（harness 驅動的接收）。

### 本章新增：隊友 loop

`run_teammate` 是第 16 章的 `serve_mailbox`，把 shutdown handshake 折了進來。被 spawn 的隊友現在會因為一筆請求而停止，而不是隨它的 daemon thread 一起死掉：

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

- shutdown 在 chat 之前先檢查，所以一般訊息不會把停止請求擠到後面。
- 發起是模型驅動的（lead 的 `StopTeammate`）；接收是 harness 驅動的（loop 確認），對應參考實作的分工。
- loop 回傳 `"shutdown"`，所以進行 spawn 的 runtime（第 13 章）能回報這次乾淨的停止。
- 第 18 章再加一個分支：inbox 為空時，從一塊共用看板認領一個 task。

### 如何接進現有架構

demo 跑一個主 agent。lead 在一個 turn 裡 spawn 一個隊友、委派、然後停止它；隊友在自己的 thread 上確認：

```python
def spawn_worker(name, team, model):                   # src/demo.py, module level
    ...                                                 # build the teammate's tools
    return run_teammate(team, name, "lead", work)       # serve_mailbox plus the shutdown handshake

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
state = next(filter(None, (lead_proto.resolve(m) for m in team.drain("lead")   # -> approved
                           if isinstance(m["content"], dict))), None)
```

- `demo()` 跑一個 `run_turn`，也就是 lead 的。它呼叫 `SpawnTeammate`、`SendMessage`，然後 `StopTeammate`。
- `StopTeammate` 送出一筆 `shutdown_request`；隊友的 `run_teammate` 確認它並返回。這次停止走的是 handshake，不是直接 kill。
- lead 把回傳的 `shutdown_approved` 解析成 `approved`。主 process 只是等待。
- plan-approval 流程就是同一套 handshake 反過來跑（先 `ExitPlanMode` 再 `ApprovePlan`），由相同的工具驅動，並在 test.py 裡驗證。
- loop 沒有改變。protocol 在管道上把請求塑形、把回覆結案，這樣包住一次 turn。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book 和 A2A 規格，也未經下面表格的系統證實。

**一次停掉一整批：**同一件事派好幾個 worker 去做，但只要一個答案。
第一個做成功的 worker 回報上來，lead 就對其餘每個 worker 各送一則停止。
demo 只停過一個隊友，不過線路上傳的東西沒有變。每一則停止都還是那套先請求再確認，
所以沒搶到的 worker 一樣會把檔案寫完、把 task 記錄收掉。就是 shutdown 流程一次送給很多人。

**同一瞬間兩個都贏：**兩個 worker 有可能同時做完。這時兩個都算第一名，lead 會送兩輪停止，結果也記成兩筆。
加一把鎖就好了。先到的那個拿到鎖，寫下誰贏了，再放掉。
第二個接著拿到鎖，看到已經有人寫了，就直接回去，不再去停任何人。不管誰先到，這件事都只結算一次。

**確認一直不回來：**等確認的停止有可能沒人回。worker 卡在一個很久的 tool call 裡，根本沒在讀 inbox。
所以停止分成兩層：lead 先問，等確認等到一個期限，期限一到就把還在跑的直接砍掉。
砍掉是備援，不是第一步。lead 一定先問，所以只要還來得及收尾，收尾就會跑。

**只有單一來源：**這兩層和那把鎖都出自書作者自己的一個實驗，不是比較過好幾個系統之後的結論。

**跟不是自己家的 agent 講話：**上面講的都假設是同一個團隊、同一個 process、同一個擁有者。
管道是共用的，成員名單在 spawn 時就知道，agent 之間也都信得過線路上的那些 id。
一跨出組織，這些就全都不成立了。沒有共用的 inbox 可以蓋 `request_id`。對方有哪些成員看不到。對方的工具清單也不能直接信。
A2A 就是為這種情況設計的 protocol。它保留請求配回覆這個核心，另外加三樣東西。

- **Agent Card discovery：**每個 agent 在一個固定的 URL 上放一份文件：名字、會做什麼、endpoint，還有要怎麼認證。
  呼叫端先讀這張卡，再決定要送什麼過去。團隊裡的名單在 spawn 時就拿到了；跨出去就得自己去抓。
- **Task lifecycle：**一次遠端呼叫是一個帶 id 的 task，狀態有 `submitted`、`working`、`input-required`、`completed`、`failed`。呼叫端拿這個 id 去輪詢或訂閱。
  `input-required` 正好是本章沒有名字的那個狀態：對面停下來要更多資訊，而 task 在等的期間還活著。
- **Opaque artifacts：**結果是以 artifact 回傳的：檔案、文字、結構化片段。對方的 trajectory 不會回傳。
  呼叫端看不到那邊是怎麼做出來的，過得來的只有結果。

**請求的狀態和 task 的狀態：**兩套做法記的東西不一樣。本章記的是一次請求：從 `pending` 走到 `approved` 或 `rejected`。
A2A 記的是一個 task：`submitted`、`working`、`input-required`、`completed`、`failed`。
差別在這筆記錄活多久。請求的記錄跟著那次來回一起結束。
task 的 id 之後還查得到：回覆收到之後、中途停下來要資訊之後、連線斷掉又接回來之後，都還查得到。
跨邊界的呼叫端兩種都要留。請求狀態講的是這一則訊息對方收不收，task 狀態講的是整件事現在做到哪。

---

## 不同系統怎麼做

一種設計如何定出請求的格式、為計畫設閘門，並乾淨地停止 agent。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **優點** | 每一次停止都經過確認，有風險的計畫都設了閘門。 | 只要照公開協定講話，任何 client 或 server 都能接上來。 |
| **限制** | 每次 handshake 都要付出往返次數和 protocol 狀態。 | 輸出要等到定案才送出，中途的進度看不到。 |
| **設計原因** | 編輯到一半被強制停掉，會留下寫一半的檔案。有風險的計畫也該先審核。 | 對面是一個你未必擁有的 process，所以用公開契約講話。 |
| **做法：message shape** | 在 `type` 上區分的 typed union，`request_id` 對應每則回覆。 | 用 session id 分辨的 JSON-RPC 方法，一個 session 同時只跑一個 prompt。 |
| **做法：plan approval** | 隊友請求後等待，lead 的回覆帶著裁決、feedback 和權限模式。 | 計畫送到人面前。被打回來時，會以帶著意見的失敗呼叫回傳。 |
| **做法：shutdown** | lead 先請求，隊友確認後才 kill。 | 先取消、再關掉輸入、再送 signal、最後強殺，每一階都有時限。 |

---

## 常見問題

- **用硬 kill 取代 handshake：**殺掉隊友的 thread 會丟掉進行中的工作，並讓它的 task 記錄變孤兒。改用先請求再確認、並把 task 標記為 `notified` 的流程。
- **孤兒請求：**一則永遠不到的回覆會讓一筆請求永遠停在 `pending`，於是 sender 一直 block。加上一個 timeout 或閒置檢查，把卡住的請求浮上來。
- **型別混淆：**只靠 id 對應回覆，會讓一則 shutdown 回覆解析掉一筆 plan 請求。檢查回覆的 variant 是否符合記錄下的請求型別。
- **審核卻不強制：**一個被審核通過的計畫，仍需要權限層來為執行設閘門（第 3 章）。在回應裡攜帶 `permissionMode`。
- **重複回覆：**一則重送的回覆可能把已經定案的狀態翻掉。任何針對非 pending id 的回覆都不做事。
- **停一整批卻沒有鎖：**兩個 worker 同一瞬間做完，兩個都算第一名，lead 就送了兩輪停止，結果也記成兩筆。
  寫下誰贏之前先拿鎖。晚一步的贏家看到名字已經在上面，就誰也不停。
- **等確認卻沒有期限：**worker 忙在一個很久的 tool call 裡，根本沒讀到請求。確認一直不來，lead 就一直等下去。
  等待要設期限，超過就直接砍。先問還是第一步，只是不再是唯一一步。
- **把遠端 task 當成一則回覆：**不是自己家的 agent 會停下來要更多資訊。那是一個 task 狀態，不是一則回覆。
  記下 task id 和它的狀態。起頭那次來回結束之後，停住的工作還找得回來。

---

## 動手跑跑看

[`src/`](src/) 承接第 16 章並加上：

- [`protocols.py`](src/protocols.py)：請求追蹤器（typed variants、correlation id、狀態機）、handshake 工具，以及 `run_teammate` loop。
- [`test.py`](src/test.py)：檢查 shutdown 與 plan 流程、各個守衛、一次工具驅動的 handshake，以及一個被 handshake 停止的自運作隊友。
- [`demo.py`](src/demo.py)：一個 lead turn spawn 一個隊友、委派，並用 StopTeammate 停止它；隊友在自己的 thread 上確認。

loop 與 subagent 路徑不變。protocol 在管道上把請求塑形、把回覆結案，這樣包住一次 turn。

```bash
python sections/17-protocols/src/test.py         # offline checks, no key
uv run python sections/17-protocols/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 的 protocol 格式](https://github.com/yasasbanukaofficial/claude-code)：`tools/SendMessageTool/SendMessageTool.ts`、`utils/teammateMailbox.ts`。
- [Claude Code plan 與 stop](https://github.com/yasasbanukaofficial/claude-code)：`tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`、`tasks/stopTask.ts`、`coordinator/coordinatorMode.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/acp/acp/README.md`、`packages/subagent/subagent-acp/README.md`、`docs/subsystems/session.md`、
  `docs/subsystems/plan.md`、`docs/subsystems/approval.md`。
- [learn-claude-code · s16_team_protocols](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter10.md`（多 Agent 协作），以中文原版為準。
  先收尾再回 ack 的停止、砍掉那一層備援，以及第一個做成功就把整批停掉、靠一把鎖讓這件事只結算一次。
  兩者都出自書作者自己的實驗，屬於單一來源。
- [A2A protocol](https://github.com/a2aproject/A2A)（Linux Foundation）：Agent Card discovery、task 生命週期的各個狀態
  （`submitted`、`working`、`input-required`、`completed`、`failed`），以及跨信任邊界的 opaque artifact 交換。
