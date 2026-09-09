# 23 · Evaluation

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> pass rate 是否可信，取決於背後的測試環境是否真的量到你在意的行為。

第 20 章記錄 production 中發生過什麼，但光有紀錄仍無法判斷品質。本章要回答另一個問題：這次修改是否真的讓 agent 變得更好？

如果系統只有一次 model call，評估很單純：送入 prompt，把輸出和標準答案比較，再計算答對比例。

換成 agent 後，這種方法就不夠了。agent 會執行多輪對話，主動向使用者補問資訊，也會透過 tool 修改環境中的資料。相同結果可能經由不同路徑完成，而且即使 agent、harness、prompt 和 model 全都相同，同一個任務跑兩次仍可能得到不同結果。

因此，agent evaluation 需要的不是一串 prompt，而是一個完整的測試環境：可 reset 的 state、模擬使用者、控制對話流程的 protocol，以及檢查最終環境狀態的 rubric。

少了這些設計，仍然可以算出分數，但數字未必有意義。題目可能早已進入訓練資料，新版本高出的 3 個百分點也可能只是抽樣波動；甚至分數看似很好，agent 卻在實際執行時退款了使用者從未提過的訂單。

---

## 核心機制

![機制圖](assets/23-evaluation.png)

完整的評估環境包含五個部分，其中四個是資料，一個負責控制流程。

- **Dataset：**一筆一筆的任務紀錄。每一筆都寫著起始 state、使用者想要什麼，以及這趟要怎麼檢查。
- **Environment state：**任務會動到的那份資料，例如訂單、檔案、資料庫。
  它要夠真實，測起來才有意義；也要抓得住，隨時能 reset 回原樣。
- **Tools：**agent 可以執行的操作。要拆到夠小（讀一筆訂單、退一筆訂單），不要弄成一個叫「解決客訴」的 tool。
- **Rubric：**一趟跑完以後，怎麼換算成分數。
- **Interaction protocol：**誰什麼時候說話，以及這一趟什麼時候結束。

第 20 章的評估介面是 `(input, grade)`：輸入一個字串、取得一個字串，再算出 pass rate。本章保留同一個入口，但在底下補上完整的評估環境。

### 新增：環境和它的 reset

state 存在環境裡，要改它只能透過環境給的 tool：

```python
def reset(self):                                       # src/evaluation.py
    self.state = deepcopy(self.initial)                # a fresh copy per episode
    self.calls = []

def call(self, name, **args):
    tool = self.tools.get(name)
    if tool is None:
        self.calls.append((name, False))               # illegal: no such tool
        return f"error: no tool named {name}"
    try:
        out = tool(self.state, **args)
    except Exception as e:                             # illegal: wrong arguments
        self.calls.append((name, False))
        return f"error: {type(e).__name__}: {e}"
    self.calls.append((name, True))
    return str(out)
```

- 每個 episode 開始前，`reset` 會把起始資料整份複製一份新的出來，agent 動到的是這份副本。
  所以下一趟拿到的還是原本那份資料，上一趟寫過什麼都看不到。
  少了這一步，退款任務跑第二次時，那筆訂單已經是退過的狀態。
- agent 叫了不存在的 tool，或參數給錯，環境會回一句話講清楚錯在哪，而不是只回一個「失敗」。
  看得懂錯在哪，agent 才可能自己改對；改不改得回來，本來就是要測的能力。
- 每次呼叫都會記下這次合不合法。這份紀錄就是過程指標的來源：叫錯幾次、總共走了幾步。就算最後結果檢查過了，這些數字還在。

### 新增：模擬使用者與 protocol

大部分 benchmark 第一則訊息就把完整需求交給 agent。真實的使用者不會這樣講話。
他們開頭只會說「我的訂單好像有問題」，剩下的你不問，他不會講。

所以模擬使用者手上有一份腳本，一輪只講一件事。這樣一來，agent 會不會主動問，才變成可以打分的能力：

```python
def run_episode(env, task, agent, max_turns=8):        # src/evaluation.py
    env.reset()
    user = task["user"]()                              # a fresh simulated user per episode
    transcript, said = [], user()
    for _ in range(max_turns):                         # the ceiling: an episode always terminates
        if said is None:
            break
        reply = agent(said, env)
        transcript.append((said, reply))
        said = user(reply)
    return {"transcript": transcript, "state": env.state, "calls": list(env.calls)}
```

可執行程式用的是固定腳本，離線檢查才會每次都一樣。實際跑的時候，是找一個 LLM 拿同一份腳本來演使用者。
prompt 裡會交代它：照角色回話、只講這一步需要講的、腳本裡沒有的不准自己編。每次講法都不一樣，但資訊釋出的順序不會變。

再做完整一點，就是讓模擬使用者對同一份 state 也有自己的 tool。有些事只有使用者做得到，agent 只能說服他動手，真實的客服電話本來就是這樣。
這時候環境不再只有 agent 在改：使用者也會動它，agent 得自己發現對方做了什麼，不能假設現在的 state 都是自己造成的。

### 新增：一趟 episode 怎麼打分

三種檢查，照順序：

```python
def grade(task, run):                                  # src/evaluation.py
    checks = {name: bool(fn(run["state"])) for name, fn in task["checks"]}       # the outcome
    said = " ".join(reply for _, reply in run["transcript"]).lower()
    told = {s: s.lower() in said for s in task.get("must_say", [])}              # what was communicated
    unsafe = [name for name, fn in task.get("veto", []) if fn(run)]              # zero tolerance
    return {"passed": all(checks.values()) and all(told.values()) and not unsafe, ...}
```

- **看終態，不看路徑：**檢查讀的是最後的 state，只要走到那個狀態，中間怎麼走都算過。標準解只是其中一種解法，不是規定要走的那一條。
- **該講的話：**錢退了，卻沒告訴客人退了多少，這趟不算做完。
  只看 state 的話，這種漏講會算成通過；只看對話的話，agent 說「已經幫您退款」但其實沒退，也會算成通過。兩邊都要查。
- **否決項：**只要踩到一次安全問題，這趟就是不過，其他項目再漂亮也救不回來：退了客人沒提過的訂單、把機密印出來、把信寄給不相干的人。

### 指標：跑 k 次是為了什麼

跑一次只能得到一個判定。同一個任務跑好幾次，才問得出真正想知道的事：

- **Pass@k：**k 次裡至少成功一次。回答的是「做不做得到」，探索型任務看這個。
- **Pass^k：**k 次全部成功。回答的是「穩不穩」，回歸測試的門檻看這個。
- **Best@k：**k 次裡最好的那次拿幾分。用在開放式任務：分數是連續的，不是只有過跟不過。

這兩個數字很快就會拉開。單次成功率 60% 的話，Pass@5 大約 99%，Pass^5 大約 8%。
指標挑錯，明明只是運氣好，看起來卻像真的做出了什麼。

另外還有一組過程指標，呼叫紀錄裡本來就有：有效呼叫的比例、跟已知的好解法比起來多走了幾步、重試幾次、每個任務花多少錢。
它們回答的是另一件事：這次過關，是省著過的，還是硬撞過的。

### 開放式輸出怎麼評

有明確終態的任務，看 state 就夠了。換成一段寫出來的文字，沒有東西可以直接比對，就交給另一個 model 照 rubric 打分。
打分準不準，幾乎都看 rubric 怎麼寫：

1. **紮根於專家：**寫進去的是這個領域的專家真正要檢查的東西，不是文字通不通順。
2. **涵蓋要夠全：**正確性、完整性、安全性都要顧到，常犯的錯要直接寫出來，不能讓評判者自己意會。
3. **有權重、有否決：**標準分成必要、重要、可選，像編造事實這種否決項一出現，總分直接歸零。
4. **每條都自己講得清楚：**每一項都要能直接判斷，不靠評判者自己的品味。
   「至少引用兩份出處，並說明各自怎麼支撐結論」可以判斷，「展現了深刻的理解」不行。

評判者本身就是 model，model 有的毛病它都有：回答越長越容易拿高分，先讀到的那一份也容易被偏袒。
評判者跟 agent 同一個家族的話，盲點也一樣，agent 犯的錯它剛好都會放過。

解法都不難：評判者換成不同家族的 model；配對比較時把順序對調，再評一次；
大規模用之前，先拿人工標好的 gold set 校準；兩邊判不一樣的，就送人工看。

[EvalGrill](https://github.com/hardness1020/EvalGrill) 把這套流程包成工具：把 agent 應用的真實案例做成 eval，先把 judge 校準好再信它。

### dataset 決定分數代表什麼

環境做得再好，dataset 不行，跑出來的就是噪音。各家 benchmark 反覆驗證出四條原則。

- **可驗證：**答案或終態不用人看就能判定。
- **分難度：**簡單、中等、困難的任務要分開，這樣「只在簡單題上有用」的改動就藏不進平均分裡。
- **人工看過：**要有人確認這題解得開、檢查方式也公平。
  有些 benchmark 之所以會出一個專門的子集，就是因為原本的題目講不清楚，或是用了不公平的測試在打分。
- **防汙染：**公開的任務會流進下一輪訓練資料。
  常見的做法有：每個任務檔案裡放 canary 字串、答案不公開、收集模型 cutoff 之後才出現的任務，以及用參數化模板從同一個題型生出新題。

### 差距要怎麼讀

兩份 build，100 個任務，70% 對 73%。這不算結果。

- **噪音帶：**n 個任務上的成功率，標準誤差大約是 `p(1-p)/n` 開根號。
  100 個任務、70% 的話大約 4.6 個百分點，所以 3 個百分點的差距整個埋在噪音裡。
- **多跑幾次：**採樣本身有隨機性，tool 回應的快慢也會影響結果，分數自然會浮動。每個設定跑三到五次，報波動範圍，不要只報一個數字。
- **配對比較：**兩份 build 跑的是同一批任務，所以逐題比，只看兩邊結果不一樣的那幾題。
  這樣就把題目難易的影響扣掉了，需要的題數也比直接比較兩個成功率來得少。
- **算一下你同時驗了幾個假設：**六個改動都用 95% 信心水準，其中至少一個純靠運氣看起來顯著的機率大約是 26%。
  要嘛把門檻收緊，要嘛把跑贏的那個獨立複跑一次，再決定信不信。

如果你預期的提升，比這套評估分辨得出來的差距還小，那接下來該做的是把任務集擴大，不是繼續調 agent。

### 從報告到一次改動

benchmark 報告只拿來做一個決定：下一步要改什麼。

1. **先懷疑評估本身：**process 被殺掉、grader 有 bug、任務跟正式環境早就對不上，這些在數字上跟 agent 變差長得一模一樣。
   動 agent 之前，先把失敗的 trajectory 讀過一遍。
2. **找失敗聚在哪：**總成功率 88%，但四個相關任務裡掛了三個，這不是整體能力不足，是缺了某一項能力。
3. **一輪只改一個變數：**model、seed、任務集、步數上限都固定住，每一輪只動一件事。一輪改三件事，什麼都解釋不了。
4. **分清楚是誰的功勞：**harness 不動，只換 model，看 model 撐起多少；model 不動，關掉 harness 的某個元件，看那個元件值多少。
   這個 repo 的主張就是：這兩個數字都存在。
5. **證據的規模要配得上決定：**四個任務可以支撐「值得跑大一點的實驗」，撐不起「可以上線」。

放進產品裡，這些會變成常駐的基礎設施。一個總開關可以關掉各種功能，量出裸 model 的 baseline。
Feature flag 負責分 AB 測試的組別，出事時也是斷路開關。
每個 commit 都存一份完整展開後的 system prompt，改 prompt 就跟改程式碼一樣，要跑一次評估。

做 AB 測試時，要把你直接動到的機制指標（計畫長度、prompt 大小）和你真正在乎的目標指標（任務成功率、單次會話成本）分開。
另外留一組護欄指標：就算目標指標變好，護欄一破也要停下實驗。

### 如何接進現有架構

評估沿用既有的 harness，沒有另外加東西：

- 環境的 tool 介面就是第 2 章的 registry，只是 handler 指向評估用的 state，不是真實世界。
- 受測的 agent 就是第 1 章的 loop，一行都沒改。loop 裡沒有任何東西知道自己正在被評估。
- 評判者就是第 21 章的 checker：另一個 agent、全新的 context、一份它只能滿足、不能改寫的 rubric。
- 第 20 章負責供料：正式環境的 trace 脫敏之後變成新任務，它的 cost tracker 給出每個任務花多少錢。
- 改進 loop 就是第 21 章的外層 loop，只是這次帶著證據：量一次、只改一件事、再量一次。

---

## 不同系統怎麼做

各個系統怎麼搭出分數背後的測試環境。

| | Claude Code | mini-swe-agent | τ²-bench | Verifiers |
| --- | --- | --- | --- | --- |
| **優點** | 爛結果在交出去之前就被擋掉。 | 跑 benchmark 的程式跟 agent 一起附在 repo 裡。 | 看終態打分，走哪條路都算過。 | 環境、harness、model 分開設定，同一套任務集誰都能評。 |
| **限制** | 原始碼裡沒有成套的評估。 | 只有 benchmark 的測試當 rubric。 | 使用者由另一個 model 扮演，分數因此會飄。 | 跑一次性的評估也要先架好整套 runtime。 |
| **設計原因** | 在同一趟執行裡就把爛結果攔下來。 | 一個任務就是一個有測試的 repo bug。 | 客服工作本來就是一段對話。 | 評估和後訓練要的是同一個環境。 |
| **做法：environment** | 重建：每次評估開一份用完就丟的工作區。 | 一個 instance 一個 container，換一個就是 reset。 | 一個領域資料庫加一份政策文件。 | 每次執行都給一個全新的沙箱 runtime。 |
| **做法：task set** | 重建：正式環境的 trace 脫敏後留成固定題庫。 | 公開 benchmark 的一個 split。 | 每個領域各自手寫。 | 用 id 載入的模組，本機或線上都行。 |
| **做法：scoring** | 一個 reviewer agent 加一份固定 rubric。 | 該過的測試要過，本來會過的不能壞。 | 終態拿去跟標準解重放的結果比對。 | task 上的 reward 函式，加一個會跑程式碼的 judge。 |
| **做法：repeats** | 同一趟執行裡重跑驗證。 | 一個 instance 跑一次。 | 同一題跑 k 次，看的是穩定度。 | 每題跑幾次 rollout 是個參數。 |

---

## 常見問題

- **只看對話，不看實際結果（Grading the transcript）：**agent 說「已退款」，跟真的退了款拿到一樣的分數。
  緩解：檢查終態，另外再檢查該講的話有沒有講。
- **任務被汙染（Contaminated tasks）：**公開 benchmark 會進到下一輪訓練資料，高分可能只是背過。
  緩解：任務檔案裡放 canary 字串、答案不公開、收集 cutoff 之後才出現的任務，以及用參數化模板生新題。
- **把噪音當結果（Reading noise as a result）：**100 個任務各跑一次，差 3 個百分點，什麼都決定不了。
  緩解：多跑幾次、逐題配對比較、差距落在噪音帶裡就當它不存在，同時驗很多改動時把門檻收緊。
- **評估壞了卻怪 agent（Blaming the agent）：**機器資源不夠、grader 有 bug、任務過期，看起來都跟 agent 變差一模一樣。
  緩解：改 agent 之前，先讀失敗的 trajectory。
- **評判者跟 agent 有同樣的盲點（Shared blind spots）：**同家族的 judge 會放過 agent 常犯的錯，偏好長回答，也偏好先讀到的候選。
  緩解：換不同家族的 judge、順序對調各評一次、先用人工標註的 gold set 校準。
- **鑽分數的漏洞（Reward hacking）：**agent 找到拿分的捷徑，跳過真正的工作：塞關鍵字、討好 judge、遇到難題就迴避。
  緩解：rubric 裡放否決項、結果指標旁邊擺過程指標，再定期人工抽檢。
- **這套評估看不出改動（A suite that cannot see the change）：**40 個任務上 2 個百分點的提升根本量不出來，每一輪都只能寫「看不出差別」。
  緩解：先把任務集擴大，再繼續迭代。
- **上一趟的 state 留到下一趟（State leaking between runs）：**沒有 reset，或只 reset 了淺的一層，上一個任務寫下的東西就決定了下一題的分數。
  緩解：每個 episode 深拷貝一份，每次執行各用一個獨立的環境（第 15 章）。

---

## 動手跑跑看

[`src/`](src/) 把 22 帶了過來，並加上：

- [`evaluation.py`](src/evaluation.py)：帶 `reset` 和呼叫紀錄的環境、一輪只釋出一項資訊的模擬使用者、episode 的 protocol、
  打分（state 檢查、該講的話、否決項）、Pass@k 與 Pass^k、二項分布的噪音帶，以及兩份 build 的配對比較。
- [`test.py`](src/test.py)：離線檢查 reset 有沒有把 state 還原、protocol 跑一趟時 agent 必須先問訂單編號、
  結果檢查明明有過卻被否決項擋下、同一個不穩定的 build 上 Pass@k 與 Pass^k 的差別，
  以及退步的 build 分數更低、配對比較能指出它弄壞了哪幾題。
- [`demo.py`](src/demo.py)：實際跑一趟並打分。
  model 扮演客服 agent，它呼叫的 tool 都打在環境上，最後 harness 看它留下的 state 給分。

loop 本身完全沒改。讓分數有意義的，是它跑在什麼環境裡。

```bash
python sections/23-evaluation/src/test.py         # offline checks, no key
uv run python sections/23-evaluation/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [ai-agent-book · 第 6 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter6.md)（《深入理解 AI Agent》，李博杰，以中文原版為準）：
  評估環境的五個要素、漸進式資訊揭露、指標詞典、rubric 四準則、統計顯著性、
  從 benchmark 報告到系統改進，以及內部評估基礎設施。
- [τ-bench](https://arxiv.org/abs/2406.12045)（Sierra）：用另一個語言模型扮演使用者；
  成不成功，是拿對話結束時的資料庫狀態跟標註好的目標狀態比對，另外還有衡量可靠度的 Pass^k。
- [τ²-bench](https://arxiv.org/abs/2506.07982) 和[它的原始碼](https://github.com/sierra-research/tau2-bench)：雙控環境，
  模擬使用者自己也有 tool；以及 reward basis（資料庫終態雜湊後跟標準解重放的結果比對、必須講到的字串、
  可選的 LLM 判定條件），各項相乘才是總分。
- [Verifiers](https://github.com/willccbb/verifiers)：環境負責安排多個 agent 之間的流程、用 id 載入任務集、
  每題可以跑多次 rollout、續跑時只補跑漏掉或出錯的那些，以及能對執行產物跑程式碼的 judge agent。
- [mini-swe-agent 原始碼](https://github.com/swe-agent/mini-swe-agent)：`run/benchmarks/swebench.py`，
  一個 instance 一個 container image，每個 instance 各自留下 trajectory 與預測紀錄。
- [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)：500 題人工驗證過的 instance，
  打分方式是原本失敗的測試要變成通過，本來就會過的測試不能壞。
- [GAIA](https://arxiv.org/abs/2311.12983)：466 題，其中 300 題的答案不公開，排行榜就抓不走。
- [BIG-bench](https://github.com/google/BIG-bench)：每個任務檔案都帶 canary 字串，避免題目被爬進訓練資料。
- [Rubrics as Rewards](https://arxiv.org/abs/2507.17746)（Scale AI）：清單式的 rubric，寫明要提到哪些事實、
  要有哪些推理步驟，以及哪些常見錯誤必須扣分。
- [Claude Code](https://code.claude.com/docs)：workflow 約定裡的 reviewer 與 judge 階段。
  內容依據 tool schema 和文件記載的行為，不是 source backup。評估套件不在原始碼裡，那幾格都標成重建。
