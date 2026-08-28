# 16 · Coordination

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> lead 依任務規模組成團隊，讓每位 agent 在獨立 thread 上工作，再透過 inbox 協作。

單一 agent 只有一個 context window，同一時間能處理的工作也有限。面對大型任務，往往需要多個 agent 同時進行。

subagent 適合處理範圍明確的子任務，但一次性的 subagent 啟動後，很難在執行途中持續溝通或調整方向。

每增加一個 agent，就會增加 token 成本，也可能讓多個 agent 對同一個檔案做出互相衝突的修改。
因此，第一個要解決的不是如何 spawn，而是團隊結構：需要幾個 agent、是否共用 context，以及誰負責指派工作。

要讓多個 agent 真正協作，系統必須提供穩定的身分、spawn 機制、可收發訊息的 inbox，以及把權限請求送回使用者的管道。

協調必須：

1. 為每個 agent 提供穩定、可尋址的身分。
2. 讓 lead 依任務規模決定團隊結構。
3. 讓每位成員在自己的 thread 上執行。
4. 讓成員主動讀取 inbox 並採取行動，不必由 harness 逐步控制。
5. 將需要核准的動作往上轉交，直到使用者做出決定。

少了 coordination，大型工作只能依序處理，或拆成一群彼此無法溝通的 worker。

---

## 核心機制

![機制圖](assets/16-coordination.png)

每個 agent 都有自己的 inbox。傳送訊息時，內容會寫進收件者的 inbox；等收件者主動讀取時，訊息才會進入它的工作流程。

團隊要有幾個人、各叫什麼名字，會由 lead 的 LLM 在執行時根據任務決定，而不是寫死在程式裡。lead 先呼叫 `TeamCreate` 組成團隊，再 spawn 每一位成員。

lead 不會親手啟動隊友。它呼叫 `SpawnTeammate`，由 harness 在背景 thread 上跑隊友的 loop（第 13 章）。
隊友接著拉取自己的 inbox 並行動，沒有任何程式在逐步驅動誰。

demo 裡沒有中央 broker。有的是名字、inbox 路徑與訊息格式的共用慣例。

- 每個 agent 擁有一個 inbox。
- 一則訊息有 sender、recipient 和 content。
- lead 呼叫 `TeamCreate` 決定名單的規模與組成；`SpawnTeammate` 再啟動每位成員。
- lead 用 `SpawnTeammate` spawn 一個隊友；那個隊友在自己的 thread 上運作。
- `to="*"` 會 broadcast 給除了 sender 以外的每一位隊友。
- sender 寫完就返回。它們不會 block 等待回覆。
- 隊友每次 poll 都會讀自己的 inbox，把新訊息併入下一個 turn。
- 權限請求走同一個管道。

### 本章新增：組成團隊

`TeamCreate` 是 lead 呼叫來決定名單規模與組成的工具。它填入一個單槽的 holder，harness 在 spawn 每位成員時讀回：

```python
def team_tools(root, me, formed):                      # src/mailbox.py
    def create(a):
        members = list(dict.fromkeys([me, *a["members"]]))   # the lead joins its own team
        formed["team"] = Team(root, members)                 # the tool call sizes and forms the team
        return f"team created: {', '.join(members)}"
    ...                                                # SendMessage stays inert until the team exists
```

- 規模和名字都沒有寫死在程式裡；兩者都由 lead 的 LLM 依任務挑選。
- `SendMessage` 在 `TeamCreate` 執行前不會生效，所以 lead 必須先組成團隊才能傳送訊息。
- `formed` 是一個單槽的 holder（ponytail：一個 in-process 的團隊登記表替身；可以用一個名單檔案作為後端，讓另一個 process 的隊友加入）。

### 本章新增：spawn 一個隊友

`SpawnTeammate` 是 lead 的模型呼叫的工具。harness 在第 13 章的 runtime 上、在自己的 thread 上啟動隊友的 loop：

```python
def teammate_tools(runtime, spawn_worker):             # src/mailbox.py
    def spawn(a):
        runtime.start(lambda: spawn_worker(a["name"]))  # section-13 thread runs the teammate's loop
        return f"spawned teammate {a['name']}; it runs on its own thread and pulls its own work"
    return [Tool("SpawnTeammate", spawn, is_read_only=True, ...)]
```

隊友的 loop 是 `serve_mailbox`：拉取 inbox、行動、重複。它在被 spawn 出來的 thread 上運作，所以隊友是自己對訊息做反應，不是被程式排好每一步：

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

- `spawn_worker(name)` 是應用端的 thunk；它為那個隊友跑一個 `serve_mailbox` loop。
- 隊友在 drain 時就把訊息拿走，所以一則訊息只會被收到一次。
- 目前還沒有優雅的停止方式。thread 是一個 daemon，會隨 process 一起死掉。第 17 章加入 shutdown handshake。
- `max_idle_polls` 為閒置等待設上界，好讓 demo 或 test 結束；真正的隊友會一直 poll，直到 process 停止。

### inbox 與權限管道

context 各自獨立的 agent 只有兩種講話的方式，跟 process 之間的那兩種一樣。
shared memory 是大家讀寫同一個地方，看到的狀態是同一份。message passing 是 sender 把一份副本寄給指定的 receiver，兩邊沒有共用任何東西。
承載這兩種的管道有三條。工具呼叫的參數只有單向，沒有回話的路。檔案重開機也還在，但需要 lock。
message bus 多了位址與順序資訊，但只有持久化到磁碟後，才能在重開機後繼續使用。
這裡的 inbox 是一個受 lock 保護的檔案，因此本質上是在共用檔案系統上實作 message passing。
team memory（第 9 章）和 task 看板（第 18 章）則是 shared memory 那一邊。
大部分團隊兩種都要：用訊息把工作發下去，用 shared memory 放那些比一則訊息活得更久的事實。

`mailbox.py` 實作一個由具名 inbox 組成的 `Team`：

```python
def send(self, frm, to, content):                      # src/mailbox.py
    targets = [m for m in self.members if m != frm] if to == "*" else [self._check(to)]
    with self._lock():                                 # serialize concurrent senders
        for t in targets:
            inbox = self._read(t)
            inbox.append({"from": frm, "to": t, "content": content})
            self._path(t).write_text(json.dumps(inbox))
```

- `_check` 在未知名稱變成路徑之前就拒絕它。
- lock 把 read-modify-write 序列化，所以並行的 sender 不會漏掉訊息。
- `drain` 讀取並清空一個 inbox。

permission bubbling 是一種 approver 的實作。它把有閘門的呼叫透過同一個管道搬給使用者：

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

1. 隊友碰到一個有閘門的工具呼叫，但它自己的 loop 前面沒有使用者可以問。
2. approver 把一則 `permission_request` 送到 lead 的 inbox。
3. lead 把它導向自己的審核 UI（這裡是 `human` callback）。
4. 裁決以 `permission_response` 的形式回到隊友的 inbox。
5. 隊友讀取那則回覆，把 allow 或 deny 回傳給閘門。

閘門仍然呼叫 `approver(name, args)`，沒有改變。答案以 inbox 訊息而非直接呼叫的形式抵達，所以升級重用了同一個管道。

沒有 `human` 時，答案必須來自別處（另一條 thread 上的 lead，或聊天平台上的一個人）。
approver 會 poll 自己的 inbox 直到 `timeout`，然後 deny：沒有人回答的權限就是不行，絕不是卡住或放行。
這對應 Hermes 的 clarify gateway：`wait_for_response` 會 block 住 agent thread，直到聊天 adapter 回答或 timeout 到期。

### 如何接進現有架構

demo 跑一個主 agent。lead 走一步，隊友就自己運作起來：

```python
def spawn_worker(name, formed, model):                 # src/demo.py, module level
    team = formed["team"]                              # whatever the lead formed with TeamCreate
    ...                                                 # build the teammate's tools
    return mailbox.serve_mailbox(team, name, work)      # the teammate pulls its own inbox

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
```

- 程式唯一寫死的輸入是 lead 的目標。lead 用 `TeamCreate` 決定團隊規模、用 `SpawnTeammate` spawn 每一位、用 `SendMessage` 委派。
- `demo()` 跑一個 `run_turn`，也就是 lead 的。隊友自己的 `run_turn` 位於 `spawn_worker`，只能透過 spawn 工具抵達。
- 每個隊友在第 13 章的 thread 上跑 `serve_mailbox`：拉取 inbox、工作、回覆。回覆數量由 lead 決定；主 process 只是等待。
- `loop.py` 維持通用。折疊與拉取 loop 屬於協調，在這個 wrapper 裡完成，不在 `run_turn` 內部。
- 權限閘門沒有改變；有閘門的呼叫仍會往上轉給 lead 審核。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book 和公開的多 agent 研究，也未經下面表格的系統證實。

**什麼時候一個團隊會贏過單一 agent：**只有當第二個 agent 能帶回第一個看不到的東西，才值得多加一個。
一份測試結果、一張截圖、一個抓回來的網頁、一個從運行中的系統問到的答案。這些叫新資訊。
如果多個 agent 只是重讀同一份文字再投票，並不會產生新資訊，只會增加 token 成本。

有兩份公開的結果講出了做錯的代價。Tran 和 Kiela 給單一 agent 和一個團隊同樣的 thinking token 預算，在他們測的那些任務上，單一 agent 跟得上。
Anthropic 則說他們的 research 團隊會用掉大約單次對話 turn 十五倍的 token。這麼貴的團隊總得帶回點什麼。

**context 是共用還是隔離：**兩個 agent 要嘛共用一份歷史，要嘛各留各的：

- **共用：**下一個 agent 直接接手全部，什麼都不用打包，也不會有事實掉在半路。
  代價是同一時間只有一個 agent 在跑，而且一個 window 要裝下整個團隊的歷史。
- **隔離：**每個 agent 有自己的 window，需要什麼就得講出來。大家可以同時跑，某個 agent 想歪了也停在自己的 window 裡。
  代價是每一次交接都得寫下來。

子任務不多、歷史裝得進一個 window、步驟本來就得照順序跑，那就選共用。其他情況就選隔離。
這個 repo 走的是隔離：subagent 每次都從空的開始（第 6 章），隊友只讀自己的 inbox。

**三種拓撲：**就算 context 隔離了，還是得決定誰跟誰講話。形狀有三種：

- **對等：**地位相同的 agent 互相傳訊息。互審和交叉檢查適合這種。
- **管理者：**一個 lead 把工作拆開、發出去、再把回來的結果合起來。子代回傳的是摘要，不是自己的歷史。
- **去中心化：**沒有 lead。每個 agent 自己決定下一棒交給誰。

本章做的是管理者。lead 幫所有人規劃，所以計畫拆錯了就是錯了，下游沒有 worker 補得回來。
這就是為什麼最強的模型要給 lead，worker 用便宜的就好。

**去中心化的團隊怎麼把工作送到人手上：**沒有 lead，工作還是得找到下一個 agent。三種公開的設計，三條路：

- **MetaGPT：**把每則訊息丟進一個 pool。每個角色訂閱自己處理的訊息類型，所以 sender 從來不用指名 receiver。
- **AutoGen：**group chat 只留一份對話記錄，由中央的 selector 決定下一個誰講話。selector 要是一直挑同樣兩個 agent，這場對話就 livelock 了。
- **OpenAI Swarm：**把每次交接做成一次工具呼叫，並限制工作最多能轉手幾次，這樣一連串交接一定會停下來。

**檔案樹的四個區域：**agent 之間靠名字找到對方，找狀態則是靠路徑。書上把這棵樹分成四個區域：

- **私有 scratchpad：**放一個 agent 自己的草稿。別人不會讀，所以完全不用協調。
- **共用工作區：**放 repo、task 看板和 team memory。每個隊友都會往這裡寫，衝突就是在這裡發生的。
  這裡需要 lock，或者一人一個 worktree（第 15 章）。
- **外部掛載：**放的是團隊自己沒做出來的東西，例如一份 checkout 或一份資料集。往這裡寫，等於動到了團隊外面的東西。
- **唯讀的內建內容：**放 skill、prompt 和工具定義（第 7 章與第 2 章）。它們在整個執行期間都不會變，所以每個 agent 看到的都一樣。

狀態放錯區域，最後會變成協調的 bug。兩個 agent 同時改一個檔案，代表那個檔案放在共用工作區。
同一個事實傳了三次，代表它本來就該寫進 team memory。

**一次 handoff 要帶什麼：**隊友看不到 lead 的對話，所以「去把壞掉的測試修好」這句話它根本無從下手。一個 handoff 包裹要帶三樣東西：

1. 任務本身，附上接收的人自己就能檢查的驗收標準。
2. 已經確認過的事實和要遵守的限制，這樣接收的人不會再查一次，也不會踩過去。
3. 檔案、log 和 branch 的路徑。

sender 的原始歷史不放進去。那東西很長、裡面都是走不通的路，還會逼接收的人把 sender 犯過的錯再讀一遍。

另一個做法是共用 context 的交接，它整個跳過包裹。一個 agent 把控制權交給另一個，整段歷史跟著過去，什麼都不會漏掉。
書上用一個在角色之間轉移控制權的工具示範這件事。那是作者自己做的實驗，當成單一來源看就好。
代價是同一時間只有一個 agent 握著控制權，所以什麼都沒辦法同時跑。寫包裹要花工夫，換來的是可以同時進行的工作。

> **接下來：** 這裡的隊友是一個沒有優雅停止方式的 daemon，而且它只對訊息做反應。
> 第 17 章加入 shutdown handshake，好讓 lead 能乾淨地結束一個隊友。
> 第 18 章加入一塊共用的 task 看板，讓閒置的隊友自己認領工作，而不是等著被傳訊息。

---

## 不同系統怎麼做

一種設計如何 spawn 出協作的 agent 並把工作分散給它們。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | 隊友能直接交談，檔案 inbox 還能跨 process 或機器。 | 子代可以從任何已連接的介面暫停、中斷。 | 一支腳本就能在硬性上限之下開出大量子代。 |
| **限制** | 檔案 inbox 有 poll 和 lock 成本，記憶體 inbox 隨 process 死。 | 沒有對等 inbox，clarify 還會卡住自己的 thread。 | 子代彼此不能講話，送訊息也不會有回覆。 |
| **設計原因** | 隊友彼此對等，需要 inbox 交談，也需要一條送回人的路。 | 協調維持 parent 對 child。 | 協調就是歸屬關係，每個子代只有一個 parent。 |
| **做法：teammates** | in-process 或 remote，各自跑自己的 loop。 | thread 上的委派子代，有暫停旗標。 | 由模型寫的腳本開出子代，長命的那種會常駐。 |
| **做法：channel** | SendMessage 寫進 inbox，也能 broadcast。 | completion queue 加 gateway RPC。 | 只有 parent 對 child。子代用 report 工具回話。 |
| **做法：shared memory** | team task list 與團隊 memory 目錄。 | 共用的 session DB，外加 lineage 標記。 | parent 的工作目錄。fork 還會複製它跑完的 turn。 |
| **做法：permission bubbling** | remote 權限請求轉成本地的審核提示。 | clarify 導向聊天平台，子代自動 deny 或 approve。 | 權限請求沿著 parent 這條線往上問。 |

---

## 常見問題

- **遺失訊息的競態：**兩個 sender 同時寫一個 inbox。用 lock 保護 read-modify-write。
- **對等 deadlock：**agent 互相等待。把訊息排入佇列並在 turn 之間 drain，而不是用會 block 的傳送。
- **權限卡住：**隊友沒有 UI 可以問使用者。把請求往上轉給 lead 代問。
- **create 之前就 spawn：**lead 在 `TeamCreate` 之前就 spawn 或傳訊息，於是沒有名單。讓兩者在團隊存在之前都保持無作用。
- **孤兒隊友：**被 spawn 的隊友在工作做完後還一直 poll。為閒置等待設上界，或用第 17 章的 handshake 停止它。
- **含糊的跨 agent 訊息：**隊友看不到 lead 的對話。改成送一個包裹：任務、驗收標準、已確認的事實、產出物路徑。
- **把 chat 當 memory 用：**耐久的共用事實屬於 team memory。
- **拜占庭式的隊友：**壞掉的 agent 不會 crash。它會回一個錯的答案，而且講得很篤定。
  重試它，或拿同一份證據去投票，拿回來的還是同一個答案。只有拿模型以外的東西去對，才抓得到。
- **共用檔案的更新被蓋掉：**兩個 agent 讀同一個檔案，然後各自寫回去，先寫的那筆就沒了。
  寫入時上 lock，或者存一個版本號，對不上就重試。
- **語意衝突：**兩邊的寫入都乾淨地套用了，結果還是壞的。一個 agent 把某個函式改了名字，另一個 agent 同時照舊名字加了呼叫。
  把工作拆開，別讓兩個 agent 管到同一件東西，或者只在一個點上合併。
- **錯誤級聯放大：**一個 agent 把某個事實搞錯了。下一個 agent 照抄，再下一個又照抄，到後來看起來就像已經確認過的事。
  只看結論的審查者會覺得前後一致。要找人去對原始證據，而且不能找產出它的那個 agent。

---

## 動手跑跑看

[`src/`](src/) 承接第 15 章並加上：

- [`mailbox.py`](src/mailbox.py)：具 locking 的具名 inbox、折疊、`serve_mailbox` loop、帶 timeout 與預設 deny 的 bubbling，以及團隊工具。
- [`test.py`](src/test.py)：檢查定址、broadcast、並行傳送、折疊、bubbling（inline、非同步與 timeout-deny）、mailbox loop，以及團隊工具。
- [`demo.py`](src/demo.py)：lead 走一步（`TeamCreate`、`SpawnTeammate`、`SendMessage`）；每個隊友拉取自己的 inbox、跑一個有閘門的 shell 任務，然後回報。

loop 與 subagent 路徑不變。協調透過 spawn 隊友、drain inbox、傳入一個 approver 來包住 turn。

```bash
python sections/16-coordination/src/test.py         # offline checks, no key
uv run python sections/16-coordination/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 工具與 inbox](https://github.com/yasasbanukaofficial/claude-code)：`tools/SendMessageTool/`、`tools/TeamCreateTool/`、`utils/mailbox.ts`、`utils/teammateMailbox.ts`。
- [Claude Code 隊友](https://github.com/yasasbanukaofficial/claude-code)：
  `tasks/InProcessTeammateTask/`、`tasks/RemoteAgentTask/`、`remote/remotePermissionBridge.ts`、`memdir/teamMemPaths.ts`。
- [Hermes Agent 原始碼](https://github.com/NousResearch/hermes-agent)：`tools/delegate_tool.py`、`tools/async_delegation.py`、`tools/clarify_gateway.py`、`tools/interrupt.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/workflow.md`、`docs/subsystems/subagent.md`、`docs/subsystems/core.md`、
  `packages/workflow/workflow-worker-thread/README.md`、`packages/subagent/tool-subagent-report/README.md`。
- [learn-claude-code · s15_agent_teams](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter10.md`（多 Agent 协作），以中文原文為準。
  context 共不共用、拓撲分類、檔案系統分區、handoff 包裹。角色互轉那個示範是作者自己的實驗。
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?*（[arXiv:2503.13657](https://arxiv.org/abs/2503.13657)）：MAST 分類法與拜占庭式的框架。
- Tran, Kiela, *Single-Agent LLMs Outperform Multi-Agent Systems Under Equal Thinking Token Budgets*（[arXiv:2604.02460](https://arxiv.org/abs/2604.02460)）。
- Erdogan et al., *Plan-and-Act*（[arXiv:2503.09572](https://arxiv.org/abs/2503.09572)）：planner 的品質就是這次執行的上限。
- Anthropic, [*How we built our multi-agent research system*](https://www.anthropic.com/engineering/multi-agent-research-system)：一個 research 團隊的 token 成本。
- [MetaGPT](https://arxiv.org/abs/2308.00352)、[AutoGen](https://arxiv.org/abs/2308.08155)、[OpenAI Swarm](https://github.com/openai/swarm)：去中心化的路由與交接次數上限。
