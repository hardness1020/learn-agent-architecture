# 22 · Graph engineering

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> 已知的流程交給程式碼控制，只有真正需要判斷時才呼叫 model。

第 21 章把多層 loop 疊在 agent 外面，本章則進一步整理多次 model call 之間的流程。

許多任務的步驟在呼叫 model 前就已經很清楚，例如先分類工單再處理、先 review diff 再 commit，或先取得核准再執行外部動作。
一般 agent loop 每次都詢問 model 下一步該做什麼，等於重新探索一次既定流程。這種 routing 慢、花 token，而且每次跑選的路都可能不一樣。

Graph engineering 的做法，是把已知流程用程式碼寫成一張有向圖（directed graph）：

1. **Node** 負責執行工作，可以是一般程式碼、一次 model call，或完整的 agent run。
2. **Edge** 決定下一個 node，由 harness 用程式碼選擇，不必再問 model。
3. **Cycle** 讓流程可以回頭，適合重試、review 後修改，或人工暫停後繼續。
4. **State** 是沿著圖傳遞的資料，每個 node 讀取目前狀態，再寫回自己的更新。

原則很簡單：已知流程寫進程式碼，只有需要語意判斷的部分才交給 model。第 21 章的 loop 就是最小的一張圖，兩個 node 加一條回邊；本章把它推廣到 node 更多、接法更自由的圖。

---

## 核心機制

![機制圖](assets/22-graph-engineering.png)

最簡單的版本只有三樣東西。一個 dict 把 node 名稱對到要跑的函式，另一個 dict 記著每個 node 跑完接誰。
再加一個 state dict，每個 node 都從裡面讀資料，也把自己的改動寫回去。

```python
def run_graph(nodes, edges, state, start, budget=20):  # src/graph.py
    state = dict(state)
    trace = []
    node = start
    for _ in range(budget):                        # the ceiling: harness-enforced
        state.update(nodes[node](state) or {})     # a node returns only its updates
        trace.append(node)
        step = edges.get(node, END)
        node = step(state) if callable(step) else step   # a coded edge: no model call
        if node == END:
            return {"ok": True, "state": state, "trace": trace}
    return {"ok": False, "state": state, "trace": trace}   # budget spent: escalate
```

- `nodes` 是一張 dispatch map（第 2 章）。node 讀 state，只回傳自己改動的 key。
- edge 可以是固定的 node 名字，每次都走同一條；也可以是一個吃 state 的函式，看當下的 state 決定。兩種都由 harness 用程式碼判斷，routing 不花任何 token。
- 沒有 edge 的 node 就是圖的終點。budget 是第 21 章的上限：cycle 撞到上限就停，回傳 `ok: False` 交給人。
- `trace` 依序記下跑過哪些 node，就是這次執行留給第 20 章的紀錄。

### Node：從純程式碼到完整 agent

每個 node 都在純程式碼和完整 agent 之間選一個位置：

- **Code node：** 解析、驗證、固定的 API 呼叫。決定性的，不花 token。
- **Model node：** 一次 LLM 呼叫，例如分類器。有限度的判斷。
- **Agent node：** 一整個第 1 章的 loop，帶著 tool。開放式的判斷，但被固定在一個位置上。

`agent_node` 把內層 loop 包裝成一個 node。每次經過時都會根據 state 產生 prompt，再用全新的 `messages[]` 執行 `run_turn`，
所以這個 node 只看得到 prompt builder 給它的部分，不是整趟執行。

怎麼選？原則就是省 token：分支條件寫得出來的，就交給程式碼；model 呼叫只留給真的需要判斷的 node。

### Edge：把寫死的規則換成一次有型別的判斷

有些分支條件很難寫成程式碼。這道指令會不會造成破壞？這張工單是不是在講帳務？
這種分支還是由 harness 決定，只是要 model 幫忙挑，就得再多跑一整輪。

第三種 edge 只問一個很窄的問題。呼叫回傳一個機率，程式碼再拿這個機率挑分支：

```python
def route(p, conf, allow_below=0.10, deny_above=0.90, conf_floor=0.45):  # src/decide.py
    if p is None:
        return "ask"                               # no layer, or the call failed
    if conf is not None and conf < conf_floor:
        return "ask"                               # too flat to act on either way
    if p >= deny_above:
        return "deny"
    if p <= allow_below:
        return "allow"
    return "ask"                                   # the band: this is where a person goes
```

`route` 仍然是用程式碼挑分支，本章開頭第 2 點要的就是這件事。
這通呼叫提供的是一個程式碼自己算不出來的機率。

- **兩個門檻，不是一個：** 只有一個切點，連答案沒把握的時候也會被逼出一個結論。
  兩個門檻中間留下一段不表態的區間；落在這段區間裡，harness 就停下來問人。
- **這一層只會收窄：** 它評的是圖上已經有的分支，不會自己多開一條，也不會把本來不准的事變成准。
  key 不見、timeout，或答案格式壞掉，都拿不到數字，那就走 `ask`，也就是檢查失敗時 harness 的預設。
- **confidence 只能讓判定更緊：** confidence 低的時候，判定會改成 `ask`，永遠不會改成 `allow`。

TypeSafe 的 Jev 收一份 state 和一組有型別的問題。
選擇題的部分，每個選項回一個機率，再加一個 confidence，表示這些機率有多集中在同一個選項上。
同一個 request 裡的問題會在同一趟全部答完，所以問五件事和問一件事的成本差不多。
回應裡不會有生成的文字，也沒有推理過程要 harness 去 parse。
什麼都不用生成，這通呼叫才小到可以掛在 harness 每一步都會經過的 edge 上。換成再叫一次 model 就掛不上去。

廠商自己在文件裡寫了三個限制。有型別的答案代表格式正確，但它還是可能是錯的。
calibration 講的是一群答案的性質，它證明不了眼前這一個答案對不對。
model 把 state 當成資料讀，不會把它當成有敵意的輸入，所以有人把想引導答案的文字寫進去，答案就真的會被帶偏。

分支條件寫得出程式碼，就用規則；條件需要判斷，就用一次有型別的呼叫；結果落在不表態的區間，就交給人。
第 3 章的 permission 規則照樣有效，有型別的檢查只能再縮限那些規則已經允許的事。

### 常見的圖形

出處裡叫得出名字的 workflow pattern，其實都是圖形：

- **Prompt chaining：** 一串 node 排成一條路，中間用程式碼把關。
- **Routing：** 一條條件式 edge，分流到各個專門的 node。
- **Parallelization：** 幾條同時跑的分支在一個 node 會合。可以是拆工作（sectioning），也可以是同一件事跑多次投票（voting）。
- **Orchestrator-workers：** 一個 node 在執行時決定要派出多少工作，再由一個 node 收攏。edge 是動態的，但整體還是一張圖。
- **Evaluator-optimizer：** 一個 worker node、一個 checker node，加一條往回的 edge。這就是第 21 章的驗證 loop，放進圖裡變成一個子圖。

各家的講法還沒統一。同樣的東西，`ai-agent-book` 用的詞是「collaboration topology」和「orchestration」，「graph engineering」它只在術語註記裡提了一句。
本章還是用自己的名字，因為它講的就是一張寫在程式碼裡的圖。你去看別的來源時，對照的是機制，不是那個詞。

### 什麼時候不要畫圖

開放式的工作沒辦法預先定好流程。深度研究和難查的 bug 需要邊跑邊規劃；事先畫死的圖，反而擋住解法需要走的那條路。
出處給的原則：只把你本來就會強制執行的流程寫進圖裡（先分類再處理、先 review 再 commit、先核准再送出），
而且只有確實改善結果的時候才多畫一段。其他的都交給普通的 loop，讓 model 自己規劃。

最常見的其實是混合式：把 agent 當成固定圖裡的一個 node。圖保證 review 一定會發生，agent 決定在自己的位置裡怎麼把事做完。

### 如何接進現有架構

本章只加了一個小元件（edge map），其他都沿用前面的：

- node 做的事就是第 1 章的 loop；`agent_node` 原封不動包住 `run_turn`。
- 程式碼判斷的 edge 沿用第 2 章的 dispatch 紀律：查表，不是 model 的輸出。
- worker 和 checker 分屬不同 node 是第 6 章；並行的分支用第 15 章的 worktree 隔離。
- step budget 和交回給人的約定是第 21 章。
- trace 交給第 20 章的 telemetry：看哪些 edge 有 fire，就知道哪些分支是死的。
- 有型別的 edge 靠兩個門檻撐著，而門檻得有人去挑。第 23 章拿標註過的紀錄來檢查挑出來的值對不對。

可執行程式接的就是上面那張圖：

```python
nodes = {                                          # src/demo.py
    "classify": lambda s: {"route": "math" if any(c.isdigit() for c in s["task"]) else "prose"},
    "math": agent_node(prompt, model, math_reg),   # a full agent run as one node
    "prose": agent_node(prompt, model, Registry()),
    "check": check_node,                           # section 21's checker, now a node
}
edges = {
    "classify": lambda s: s["route"],              # a coded edge: routing costs no tokens
    "math": "check",
    "prose": "check",
    "check": lambda s: END if s["verdict"]["passed"] else s["route"],   # the cycle
}
```

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book，也未經下面表格的系統證實。

**Phase node：** phase node 把一件工作拆成好幾個階段來跑，每個階段共用同一份 `messages[]`。
Explore、implement、review 是同一件工作的三個階段，不是三件工作。
前一個階段查到什麼，trajectory 就帶到下一個階段，所以沒有哪個階段需要把任務從頭再讀一遍。

**每個 phase 的 tool：** 每個 phase 有自己的 system prompt，也有自己的一套 tool，換 phase 的時候 harness 兩樣一起換掉。
history 原封不動留著，所以沒有東西要打包給下一個 phase。書裡寫的三個 phase 是：

- **Explore：** 讀取和搜尋。
- **Implement：** 編輯和執行。
- **Review：** 讀取，再加一個回傳結論的 tool。

**Gate tool：** model 想離開一個 phase，就呼叫一個 gate tool，例如 `finish_exploring`。
harness 把這個呼叫當成 edge，接著開始下一個 phase。gate 是唯一的出口，所以一個 phase 什麼時候結束，是 harness 說了算，不是 model。

**流程：** 先跑 explore，再跑 implement，最後 review。review 沒過就把執行送回 implement，
implement 接著往下做，review 寫的東西本來就在 trajectory 裡。用本章的講法，這就是一條路加一條往回的 edge，
跟前面的 evaluator-optimizer 是同一張圖。

**要掛哪一種：** 分支之間沒關係，就用全新的 `messages[]`；幾個 node 是同一件工作的不同階段，就留同一條 trajectory。
全新的 `messages[]` 讓每個 node 的 window 都很小，分支之間也互不干擾。
只保留一條 trajectory 的好處，是前面找到的資訊都還看得到；代價是流程愈長，占用的 context window 就愈多。這屬於 context 分配問題（第 8 章）。

**這樣算不算 multi-agent：** 書把這個做法算成 multi-agent，理由是每個 phase 的 prompt 和 tool 都換掉了。
這個 repo 則算成同一個 agent 換了 prompt 和 tool。用哪個名字，機制都是同一個，所以引用這個結果的時候，先講清楚你用的是哪個定義。

**只有一個來源：** 這個做法的依據是書裡自己做的實驗，沒有第三方的報告佐證。

---

## 不同系統怎麼做

各個 agent 怎麼決定下一步跑什麼。

| | Claude Code | Hermes Agent | mini-swe-agent |
| --- | --- | --- | --- |
| **優點** | Routing 是程式碼：不花 token、不會變來變去。續跑時跑完的 node 從紀錄重放。 | 不用事先畫圖，任務怎麼走，流程就怎麼走。 | 整張圖一眼就能看完。 |
| **限制** | 圖活在單次執行的 script 裡，不是可以重用的宣告式圖。 | Routing 花 model 的 token，每次跑可能不一樣。 | 所有任務都走同一條流程，沒有分支可以特化。 |
| **設計原因** | 把編排當成程式：script 寫好一次，harness 每次都決定性地執行。 | 假設助理型工作太開放，流程沒辦法事先宣告。 | 所有選擇都留在 model 裡，harness 只留一個 cycle。 |
| **做法：nodes** | 一個 node 一個 subagent，回傳通過 schema 驗證的結構化輸出。 | 委派出去的 subagent，深度和並行數都有上限。 | 兩個：一個 model step、一個 environment step。 |
| **做法：routing** | 階段之間用普通的 script 程式碼：條件、迴圈、平行分派。 | model 用 tool call 選路，沒有寫在程式碼裡的 edge。 | 一個固定的 cycle，跑到 model 提交或 budget 用完為止。 |
| **做法：state** | 階段的回傳值往下傳；journal 記下每個 node 的輸出供續跑。 | 結果經過 completion queue 回到呼叫端。 | message list 就是全部的 state。 |

---

## 常見問題

- **Model 當 router（Model as router）：** 把選路交給 model，燒 token、增加延遲，而且每次跑不一樣。最上游選錯一次，後面全部跟著錯。
  緩解：下一步走哪個 node，一律用程式碼判斷；model 呼叫留給需要判斷的 node。
- **把機率當成證據（Probability read as proof）：** 有型別的答案格式永遠正確，光這一點就足以讓它看起來是對的。
  緩解：不表態的區間留著；這一層只能拿掉分支，不能放行分支。
- **agent 自己寫得出來的證據（Evidence the agent can write）：** 有型別的 edge 讀到的 state 可能夾著 model 自己的輸出，而那段輸出會改變 gate 的答案。
  緩解：這份 state 只放 harness 自己掌握的欄位；這一層拿來選路，不要當成安全邊界。
- **門檻只調過一次（Thresholds set once）：** 門檻是照著某一版 model 和某一種流量配出來的，後來兩邊都在變，門檻卻沒再動過。
  緩解：先只記錄決策、不真的照做；再拿那份紀錄重配一次；問題的文字和門檻一起做版本控管（第 23 章）。
- **過度畫圖（Over-graphing）：** 需要探索的任務被固定的圖框住，解法要走的路被擋掉。
  緩解：只把本來就會強制執行的流程寫進圖裡；開放式的工作留給普通的 loop。
- **沒有失敗的路（No failure edge）：** 負責檢查的 node 遇到 FAIL 卻無路可送，爛輸出就一路流到下游。
  緩解：每個檢查 node 都給一條帶 budget 的往回 edge（第 21 章）。
- **沒有上限的 cycle（Unbounded cycle）：** 沒有上限的重試 edge 會永遠繞下去。緩解：harness 強制執行的 step budget；budget 用完就交給人。
- **State 膨脹（State bloat）：** 每個 node 都把完整輸出倒進共用的 state，後面的 node 被淹沒。
  緩解：嚴格的 state 邊界；node 只讀需要的子集，只回傳自己的更新（第 8 章）。
- **跑到一半掛掉（Mid-run death）：** 一張長圖在第七個 node 掛掉，重來卻從第一個 node 開始。
  緩解：記下每個 node 的輸出；續跑時跑完的 node 從紀錄重放（第 11、12 章）。
- **Phase 走不完（Phase that never ends）：** model 一直不呼叫 gate tool，這個 phase 就用同一份 prompt、同一套 tool 一直做下去，只有 budget 停得了它。
  緩解：gate 是唯一的出口；每個 phase 各自有 step budget；budget 用完就往下一個 phase 走，或者交給人。
- **Trajectory 一路長下去（Trajectory that carries every phase）：** 只有一條 trajectory，每過一個 phase 就長一截。裡面還留著現在沒掛的 tool 的呼叫紀錄，model 可能會再叫一次。
  緩解：在 phase 的 prompt 裡寫清楚現在是哪個 phase、有哪些 tool；叫到沒掛的 tool 就回一個清楚的錯誤；跑完的 phase 拿去 compact（第 8 章）。

---

## 動手跑跑看

[`src/`](src/) 把 21 帶了過來，並加上：

- [`graph.py`](src/graph.py)：`run_graph`（node 的 dispatch map、固定和條件式的 edge、在 node 之間傳遞的 state、step budget）和 `agent_node`，把內層 loop 掛成一個 node。
- [`decide.py`](src/decide.py)：`route` 用兩個門檻判定，中間留一段不表態的區間；`decision_edge` 把判定接到分支上。
  離線用的提問器回傳錄好的答案，線上那個走 stdlib http。
- [`test.py`](src/test.py)：離線檢查涵蓋串接順序、state 合併、純程式碼的 routing、cycle 撞到 budget 就停，以及 agent node 每次經過都拿到全新的 `messages[]`。
  另外還檢查三選一的區間、標成危險的一律不給 `allow`、拿不到答案就走 `ask`、機率調高判定不會變鬆，以及打斷人的次數上限。
- [`demo.py`](src/demo.py)：照著圖跑一趟。code node 分類，有型別的 edge 判斷能不能往下走，agent node 作答，
  第 21 章的 checker 打分，沒過就帶著 feedback 繞回去。
  沒設 `TYPESAFE_API_KEY` 的話，gate 讀的是錄好的答案，所以跑這個 demo 只需要 Anthropic 的 key。

loop 本身完全沒改。什麼時候輪到它跑，由圖決定。

```bash
python sections/22-graph-engineering/src/test.py         # offline checks, no key
uv run python sections/22-graph-engineering/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [LangChain · 3 years of graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph)：node、edge、cycle、把 agent 當 node，以及什麼時候不要畫圖。
- [Anthropic · Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)：workflow 與 agent 的分界，加上五種 workflow 圖形。
- [Google · Why we built ADK 2.0](https://developers.googleblog.com/en/why-we-built-adk-20/)：用程式碼選路、node 之間的 context 隔離、在 workflow 的 node 上掛 agent。
- [Claude Code](https://code.claude.com/docs)：`Workflow` script 的約定（pipeline、平行分派、結構化輸出、續跑）。內容依據 tool schema 和文件記載的行為，不是 source backup。
- [Hermes Agent 原始碼](https://github.com/NousResearch/hermes-agent)：`tools/delegate_tool.py`、`tools/async_delegation.py`、`batch_runner.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/workflow.md`、`packages/workflow/tool-workflow/README.md`：每次執行由模型現寫腳本，不留下任何圖。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py` 的 run loop 與 budget、`run/benchmarks/swebench.py`。
- [TypeSafe Jev 文件](https://docs.typesafe.ai/api.md)：一個 request 裡放一份 state 和一組有型別的問題，
  選擇題的答案則是每個選項一個機率，外加一個 confidence。
  [Limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13) 寫的是有敵意的 state 和 context rot。
  [System One concepts](https://docs.typesafe.ai/concepts/how-to-build-with-system-one) 講清楚這個 model 的角色：
  它回答軟體丟過來的問題，不是拿來當 agent 用，它不會自己決定下一步做什麼。
  本章引的是文件寫明的約定和限制；廠商跑出來的延遲和價格結果，這裡並沒有獨立複現過。
- [ai-agent-book · 第 10 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md)（《深入理解 AI Agent》，李博杰，多 Agent 协作，以中文原版為準）：
  在同一條 trajectory 上做多階段角色轉換：每個 phase 一份 system prompt 和一套 tool，phase 之間用 tool call 當關卡，review 可以繞回實作。
  這個做法的證據只有書裡自己做的實驗。同一章主要用的詞是「collaboration topology」和「orchestration」。
