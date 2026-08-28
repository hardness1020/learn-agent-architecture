# 21 · Loop engineering

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 重點不再是下一句 prompt，而是如何設計一套能自行啟動、驗證與改進的 loop。

前面各章分別在 model call 周圍加入一項機制，本章則把這些元件組合成完整系統。

Loop engineering 代表的是工程重心的轉移。與其由人逐個 turn 下 prompt，不如建立外層系統，讓它自己找出工作、啟動 agent、檢查輸出並決定下一步。人的角色也從逐步操作，轉為設計規則與邊界。

外層 loop 必須：

1. 由 trigger 啟動，不只依賴 user 輸入（第 14 章）。
2. 輸出通過檢查後，任務才算完成。
3. 靠預先設定的 budget 停下來，而不是靠運氣。
4. 保存狀態，讓下次執行能延續進度（第 9、12 章）。
5. 即使沒有人即時監看，也要留下完整回報（第 20 章）。

少了這一層，人就得親自充當外層 loop：下 prompt、讀輸出、判斷結果並手動重試。人一離開，agent 也會跟著停止。

---

## 核心機制

![機制圖](assets/21-loop-engineering.png)

最簡單的理解方式，是在 agent loop 外再加上三層 loop。每一層負責回答不同問題。

1. **Agent loop**（第 1 章）：呼叫 tool 直到任務看起來完成。回答的是：這一步怎麼做完。
2. **驗證 loop（verification loop）：**拿 rubric（評分準則）替輸出評分。沒過就帶著 feedback 重試，最多試到 budget 用完。回答的是：是不是真的完成了。
3. **事件 loop（event loop）：**cron 排程、webhook 和 channel 負責啟動執行（第 14、19 章）。回答的是：工作什麼時候開始。
4. **改進 loop（improvement loop）：**trace 和 eval（第 20 章）回頭改 harness 設定、skill 或 model。回答的是：整個系統有沒有變好。
   這個 loop 成熟到極致時，改的是 harness 本身：從 trace 裡挖出弱點、提出一個範圍受限的修改、再用 regression 測試驗證。
   loop 的結構本身變成一個可以搜尋的空間，而不是手工設計的模板。

資料由內往外流。trigger fire 之後把一個 prompt 放進 queue。agent loop 產出一個候選輸出，評分者替它打分數。
沒過而且 budget 還有剩，就帶著 feedback 重試；過了就透過該 task 的 channel 投遞出去。
這次執行做了什麼，會記錄成 trace 留在 telemetry（第 20 章）。改進 loop 之後就是讀這些紀錄，來決定 harness 哪裡該改。

### 本章新增：驗證 loop

這是前面章節唯一沒做過的 loop。內層 loop 是 model 自己說完成就停。有了驗證 loop，「完成」不再是 model 說了算，要通過檢查才算數：

```python
def verified_run(task, worker, checker, budget=2):    # src/verify.py
    feedback = ""
    attempts = []
    for n in range(1, budget + 1):                    # the ceiling: harness-enforced
        out = worker(task + feedback)                 # the inner loop (section 1)
        verdict = checker(task, out)                  # a separate checker (section 6)
        attempts.append({"attempt": n, "passed": verdict["passed"], "reason": verdict["reason"]})
        if verdict["passed"]:
            return {"ok": True, "output": out, "attempts": attempts}
        feedback = f"\n\nA prior attempt was rejected... Why it failed: {verdict['reason']}"
    return {"ok": False, "output": None, "attempts": attempts}   # budget spent: escalate
```

- 評分者是另一個 agent，用全新的 context（第 6 章）。讓 worker 評自己的輸出，多半都會給過。
  `agent_checker` 做的就是這件事：每次評分都在新的 `messages[]` 上跑內層 loop，verdict 的第一個字是 PASS 或 FAIL。
- rubric 定在 loop 之外。model 只能想辦法滿足它，不能改寫它。
- feedback 是資料。沒過的 verdict 會併進重試的 prompt，所以第二次嘗試知道第一次錯在哪。
- `ok: False` 是要交給人接手的訊號。嘗試紀錄會一併交出去；loop 不會永遠重試下去。

只有一個過或不過，訊號太薄。把判決拆成三個問題，每個問題都要自己說得出證據：

- **結果（outcome）：**這趟執行有沒有留下正確的狀態。證據就是那個狀態，能用程式檢查的就用程式檢查（第 23 章）。
- **過程（process）：**規則有沒有守住：能用哪些 tool、順序對不對、該確認的有沒有跳過。證據是 trace 裡的 tool 呼叫。
- **品質（quality）：**程式檢查不出來的那部分，答案夠不夠好。證據是 rubric，而且要指名是哪一條沒過。

可執行的 src 只評第三個問題。結果和過程要有一個會記錄過程的環境才檢查得了，那是第 23 章在做的事。
拆開的好處是知道該修哪裡。結果過了、過程沒過，代表這趟只是運氣好。過程過了、結果沒過，代表規則本身寫錯了。

### Budget 與停止條件

每個 loop 都需要一個 model 說什麼都繞不過去的上限：迭代次數、token budget、時間上限，或 dry counter（連續 K 輪都沒有新發現就停）。

上限由 harness 強制執行。拜託 model 自己停下來只是提示，不是停止條件。
在 `verified_run` 裡，上限就是 `range()` 的邊界：第 `budget + 1` 次嘗試不可能發生。

### 成熟度等級

Loop engineering 的幾個出處都用「敢讓它做多少事」來替 loop 分級：

- **L1 · 回報：**loop 只讀取和回報。動手的是人。
- **L2 · 協作：**loop 起草修改。由人核准。
- **L3 · 無人看管：**loop 直接動手。人事後稽核。

等級是一個權限決定（第 3 章）。只有在目前等級的輸出已經穩定到讓人覺得無聊時，才把 loop 升一級。

### 如何接進現有架構

本章沒有加任何新的基本元件。它是前面各章的組合：

- trigger 是第 14 章的 schedule 和第 19 章的 channel。
- worker 是第 1 章的 loop；maker 和 checker 的分工用第 6 章的 subagent。
- 平行的 loop 用第 15 章的 worktree 隔離。
- 執行之間的狀態放在第 9 章的記憶和第 12 章的 task 紀錄。
- 回報和 trace 是第 20 章。改進 loop 把第 20 章量到的東西接回 harness 的修改。

可執行的 src 也是同一套組法。`run_turn` 完全沒改，跟第 20 章一模一樣；`verified_run` 只是在外面多包一層驗證：

```python
def worker(prompt):                                # src/demo.py · the inner loop, unchanged
    return run_turn([{"role": "user", "content": prompt}], model, reg, Session(mode=DEFAULT))

checker = agent_checker(RUBRIC, model)             # a fresh grader agent, no tools
result = verified_run("What is 27 + 15? Use the add tool.", worker, checker, budget=2)
```

本章新加的是紀律：說完成之前先評分、開始之前先設 budget、無論如何都要回報。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book 和已發表的自我改進研究，也未經下面表格的系統證實。

**學到的東西該放哪：**假設某趟執行發現 staging 資料庫要換一組連線字串，這件事該存到哪？
改進 loop 難的不是找出教訓，而是挑一個地方放。可以放的地方有四種：

- **知識文件：**某趟執行發現的一個事實。寫進去便宜，刪掉也便宜。任務需要的時候 agent 再讀回來（第 9 章）。
- **Prompt 或 skill：**一種希望每次都重複的行為。代價是只要載入，每個 turn 都得付 context（第 7 章）。
- **程式：**一段每次都跑得一模一樣的流程。推論的時候不花錢，而且測得起來（第 2 章）。
- **模型權重：**最後手段。慢、貴、最難反悔，也不在這個 repo 談的 harness 範圍裡。

規則是挑裝得下這個改動、而且最小的那一個。最小同時也代表最好驗、最好收回。
連線字串是一個事實，所以寫進文件，不要塞進 system prompt。

第二個去處，也就是 prompt 或 skill，最容易被濫用，所以要另外設關卡。
要改，就從發生過好幾次的失敗來改，不要只憑一次跑壞。
寫清楚它什麼時候才適用，不相干的執行才不會被它影響。接著驗兩次：一次用改動附近的案例，一次用寫的時候沒看過的 holdout set。
先上一部分流量，回退方案隨時備著。
Karpathy 把這件事叫 system prompt learning：改的是文字，不是權重。
ACE 讓每次改動都很小，只去改 context 裡編了號的單項，不整段 prompt 重寫。

**從用 tool 到做 tool：**第三個去處，也就是程式，是前面章節沒做過的。
skill 交給 model 的是一份它還得自己讀、自己照做的指示（第 7 章）；編譯出來的 workflow 交給 harness 的則是一段不用 model 就能跑的程式。
假設 agent 已經訂過十次同一類的票，把它變成程式有五步：

1. **捕捉（capture）：**把一趟跑成功的執行記下來：呼叫了什麼、順序如何、每一步前後的狀態長什麼樣。
2. **參數化（parameterize）：**每趟不一樣的地方改成參數，一直沒變的部分就是那段程式。
3. **在重置環境驗證（validate on reset）：**在乾淨的環境重放一次（第 23 章）。每一步跑之前檢查一次、跑完再檢查一次，最後還要看整體狀態對不對。
4. **重放（replay）：**下次遇到同類任務就直接跑這段程式。不用呼叫 model，所以又快又便宜，每次結果也都一樣。
5. **失效（invalidate）：**只要有一項檢查沒過，這段程式就退場。任務交還給 model，由它去捕捉新的一段。

做 tool 走的是同一條路，只是從另一頭開始：agent 遇到自己做不到的事，就去找現成的函式庫，把它包成一個 tool，驗證過了 registry 才收（第 2 章）。
兩件事都是把一次昂貴的探索，換成一個便宜又檢查得動的能力。
兩件事也都少不了第五步，因為當初依賴的那個網站或 API 一定會變。

**改 harness 本身：**假設這個 loop 想改的不只是 prompt，而是 harness 的程式碼。那它要先有一份契約，才輪得到 patch。
這份變更契約要寫四件事：哪些 trace 失敗、失敗得多頻繁、根本原因是什麼、這次改動預期改善什麼，還有怎麼收回來。
沒有契約就不准動手。契約是給人看的，有沒有它，決定了這是一個能自我修改的 loop，還是一個沒人審得動的 loop。
它能改哪些程式碼要事先講明。權限、budget 和關卡都放在那個範圍外面，這個 loop 就碰不到（第 3 章）。

loop 能搜的範圍是一道階梯。最底下那階是 prompt 裡的一條規則。
往上依序是 context 怎麼組裝、workflow 有哪些步驟、harness 程式碼，最上面是那段負責提出修改的程式。
下面那階失敗了才往上爬一階。每爬一階，能搜的東西更多，能驗的東西更少。
一條 prompt 規則，一天就能做完 A/B 測試；換掉提出修改的那段程式，之後每一個修改的產生方式都跟著變。

**線上執行，離線學習：**這兩件事要分開。線上的 loop 只把任務跑完、把過程記下來。
它不做提煉、不升級 skill，也不改 prompt。
另外一個離線的 loop 才把很多趟執行一起讀，找出反覆出現的失敗，寫出候選改動，驗證它們，再發布成一個版本。

分開之後，單獨一趟執行就改不動整個 agent。一條走運的路徑不算規律。
某個網頁叫 agent 記住的話，更不算證據。
要求好幾趟執行都有同樣的訊號，再加上一道驗證關卡，這兩種東西就進不了正式發布。

拆開之後，該量的東西也變了。要看的是兩個數字，不是一個：

- **更新（updating）：**這個 loop 產出的候選好不好。提了幾個、幾個通過驗證、幾個被收回去。
- **收益（benefit）：**發布出去的改動有沒有用。該載入的執行有沒有載入、載入之後 agent 有沒有真的照做、holdout 上的表現有沒有變好。

兩個都要看。只看第一個，一個內容正確但從來沒被載入的 skill，看起來就像一次失敗的更新，loop 對自己的判斷也就跟著錯了。

---

## 不同系統怎麼做

各個 agent 如何組合自己的外層 loop。

| | Claude Code | Hermes Agent | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- | --- |
| **優點** | verify 用程式編排，budget 是硬上限。 | 有 budget，改進也能回滾。 | 每趟 run 的帳單都有硬上限。 | 外層 loop 以 plugin 掛在公開的事件上。 |
| **限制** | 改進 loop 在原始碼中沒有閉環。 | 沒有內建的評分重試 loop。 | 只做了 budget 這一半。 | 沒有東西檢查成果，只有輪數當預算。 |
| **設計原因** | 把外層 loop 當成一段可編排的程式。 | 目標是讓改進閉合到 model。 | 一趟 run 就是一個評分任務。 | loop 本身就是 plugin，控制自然掛在它上面。 |
| **做法：verification** | verify 階段用程式編排：judge panel。 | maker 和 checker 分工，加離線測試。 | 沒有，SWE-bench 離線評分。 | 沒有內建，做完了沒由模型自己說。 |
| **做法：event loop** | Cron、自訂節奏喚醒、remote trigger。 | gateway cron 加受限 toolset。 | 沒有，runner 排的是任務，不是時間。 | 提醒從 log 重放，以一個 turn 的形式進來。 |
| **做法：improvement loop** | workflow 可斷點續跑，從 cache 重放。 | run 會變成訓練資料。 | 沒有，只有 budget。 | 沒有現成的，但接的地方都留好了。 |

---

## 常見問題

- **沒有停止條件（No stop condition）：**沒有上限的重試 loop 會一直燒 token，直到有人看到帳單。緩解：由 harness 強制執行的迭代、token 和時間 budget。
- **自己評自己（Self-grading）：**worker 給自己的輸出打分數，驗證 loop 等於什麼都沒驗。緩解：獨立的 checker agent，加上定在 loop 之外的 rubric。
- **評什麼都過（Rubber-stamp rubric）：**永遠給過的評分者比沒有還糟，因為它替爛輸出蓋上「已驗證」的章。
  緩解：對抗式驗證（要求 checker 想辦法推翻），加上定期的人工抽查。
- **太早放手（Unattended too early）：**L1 的回報從來沒人核對過，loop 就拿到了 L3 的寫入權限。
  緩解：成熟度階梯一次只升一級，由第 3 章的權限把關。
- **無聲劣化（Silent drift）：**無人看管的 loop 越跑越差，卻沒有人讀它的輸出。緩解：heartbeat、一律投遞的回報，以及第 20 章對通過率和成本的量測。
- **狀態失憶（State amnesia）：**每次執行都重新發現同樣的工作、重做一遍。緩解：把發現存進記憶或 task 紀錄（第 9、12 章），並在執行開始時讀取。
- **自我修改的 harness 繞過關卡（Self-editing harness escapes its gates）：**能改 harness 程式碼的改進 loop，也能改那些把關它的程式碼。
  緩解：權限和 budget 放在這個 loop 改不到的地方（第 3 章）。
- **代理目標偏移（Proxy goal drift）：**開放式的工作裡，rubric 只是真正目標的替身。loop 學會的是滿足 rubric：
  挑熟悉的寫法、把雜訊當成發現、只留下有過的執行。分數一路往上，真正的目標卻在偏。
  緩解：失敗的執行也留在證據裡、定期換新的 holdout set，並找人拿真正的目標來抽查輸出。
- **每個教訓都變成改 prompt（Every lesson becomes a prompt edit）：**prompt 最好寫，所以什麼都往裡面塞，塞到自己的規則互相打架。
  緩解：看教訓是什麼再挑地方放。事實寫進文件、流程寫成程式，prompt 只留給非重複不可的行為。
- **編譯好的 workflow 活得比環境久（A compiled workflow outlives its environment）：**網站或 API 已經改了，這段程式還是照跑，寫錯狀態的速度比 model 還快。
  緩解：重放時每一步前後都檢查，第一項檢查沒過就讓這段程式退場。
- **線上執行自己升級自己的教訓（The online run promotes its own lessons）：**執行途中就在提煉經驗的 agent，可能把一條剛好走運的路徑升上去，
  也可能把不可信網頁故意留給它記住的文字升上去。緩解：線上的 loop 只記證據，其他都不做。發布前由獨立的離線流程驗證候選。

---

## 動手跑跑看

[`src/`](src/) 把 20 帶了過來，並加上：

- [`verify.py`](src/verify.py)：驗證 loop（`verified_run`：評分、帶 feedback 重試、budget、交回給人）和 `agent_checker`，每個 verdict 都由一個全新的評分者做出。
- [`test.py`](src/test.py)：離線檢查第一次就通過、feedback 有進到重試、budget 上限，以及 PASS/FAIL 的 verdict 約定。
- [`demo.py`](src/demo.py)：實際跑一次 verified run：worker 帶著 add tool，獨立的 checker 按固定 rubric 評分，budget 用完就交回給人。

loop 本身完全沒改，驗證那一層是包在外面的。

```bash
python sections/21-loop-engineering/src/test.py         # offline checks, no key
uv run python sections/21-loop-engineering/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/core.md`、`packages/workflow/tool-ralph/README.md`、`packages/schedule/schedule/README.md`、`docs/subsystems/goal.md`。
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering)：building block 與成熟度分級。
- [LangChain · The art of loop engineering](https://www.langchain.com/blog/the-art-of-loop-engineering)：四層堆疊的 loop。
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/)：building block 的組合方式。
- [MindStudio · What is loop engineering](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows)：目標條件。
- [Lilian Weng · Harness engineering for self-improvement](https://lilianweng.github.io/posts/2026-07-04-harness/)：深入談改進 loop；關卡要放在 loop 之外。
- [ai-agent-book · 第 8 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter8.md)（《深入理解 AI Agent》，李博杰，以中文原版為準）：
  驗證的三層、學到的東西該放哪與那條分流規則、改 prompt 的關卡、變更契約、meta 優化階梯、
  線上與離線的拆分、進化指標的拆分，以及可驗證閉環的邊界。
- [PreAct](https://arxiv.org/abs/2606.17929)：把 trajectory 編譯成帶參數的 workflow，配上前置、後置與存檔前檢查，之後不用 model 就能重放。
  它的第一作者與本書作者同名，所以論文報的重放加速（大約 8.5 到 13 倍）當單一來源看待。
- [Alita](https://arxiv.org/abs/2505.20286)：能力缺口觸發工具創建，驗證過了才進能力庫。
- Karpathy ·「system prompt learning」（X，2025 年 5 月 11 日）：改文字而不是改權重，被當成第三種學習範式。
- [ACE](https://arxiv.org/abs/2510.04618)：用穩定的 id 增量修改 context 單項，而不是整段 prompt 重寫。
- [Lin et al.](https://arxiv.org/abs/2605.30621)：harness 更新和 harness 收益分開量，用換 model 的方式把兩者分辨開來。
- [AHE](https://arxiv.org/abs/2604.25850) 與 [Self-Harness](https://arxiv.org/abs/2606.09498)：harness 自我修改時的變更契約與受限候選空間。
- [Claude Code](https://code.claude.com/docs)：`/loop` skill、`ScheduleWakeup`、`Workflow` schema。依據 tool schema 與文件記載的行為描述，非 source backup。
- [Hermes Agent 原始碼](https://github.com/NousResearch/hermes-agent)：
  `agent/iteration_budget.py`、`cron/scheduler.py`、`tools/skill_manager_tool.py`、`hermes_cli/curator.py`、`agent/trajectory.py`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py` 的 `AgentConfig` 與 `query()`、`agents/interactive.py`、`run/benchmarks/swebench.py`。
