# 8 · Context management

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 控制 context 的大小，讓長時間 session 仍能穩定運作。

`messages[]` 會隨執行時間持續成長。每個 tool 結果、assistant 回覆和 user turn 都會增加內容，長時間 session 最後一定會逼近模型的 context limit。

context management 會在下一次 model call 前整理舊內容，視情況刪除、換成 stub、保存到外部，或濃縮成摘要，讓 session 能繼續使用。

當 context 被填滿時：

1. API 可能直接拒絕請求。
2. 每次呼叫都會變慢、變貴。
3. 過時或低價值的內容會干擾當前任務需要的資訊。

第 3 種情況稱為 context rot。無關內容愈堆愈多，模型就愈難找到真正重要的資訊。早在 context window 塞滿之前，這件事就已經開始，agent 看起來仍能運作，但判斷品質會逐漸下降。

因此，壓縮不只是為了省空間和成本。in-context learning 很大一部分是在檢索資訊：單一、明確的事實容易找到，散落在幾十輪對話中的線索則很難重新拼起來。
與其讓模型每次都從頭推導，不如先整理並保留結論。摘要做得好，即使 context window 還沒滿，也能提升回答品質。

沒有這一層，長任務終究會因 prompt 過大或資訊過於混亂而失敗。

---

## 核心機制

![機制圖](assets/08-context-management.png)

在摘要之前先用低成本的 reducer。低成本的 reducer 是在地處理，而且大致上不損失資訊。摘要則要付出一次 model call，而且可能遺失細節。

Claude Code 採用分層的順序：

```text
budget   -> 把巨大的 tool 結果存到硬碟，留下一段預覽
snip     -> 丟掉中段的舊輪次，保留開頭和最近的結尾
micro    -> 把舊的 tool 結果本體換成一個 stub
collapse -> 可選的獨立 context 系統
auto     -> 用 LLM 把整段歷史摘要成一則訊息
--- 以上都做了還是 prompt_too_long 時 ---
reactive -> 截掉開頭並重新摘要，有重試上限
```

順序很重要。舉例來說，大型的 tool 結果應該先被持久化，之後任何 pass 才可以用 stub 取代它的本體。

### 本章新增：縮減 pass

```python
def manage(messages, summarizer=None):                 # src/context.py, run every turn
    _budget(messages)                                  # persist huge results   (lossless)
    _micro(messages, KEEP_RECENT)                      # stub old result bodies (cheap)
    if summarizer and estimate_tokens(messages) > TOKEN_LIMIT:
        return _auto(messages, KEEP_RECENT, summarizer)  # summarize history (lossy, last resort)
    return messages
```

- `manage` 在每個 turn 執行低成本的 pass。
- `_budget` 把過大的 tool 結果寫到硬碟，並留下一段簡短的 preview。
- `_micro` 把舊的 tool 結果本體換成 stub。
- `_auto` 保留第一個 turn 和最近的尾端，然後摘要中間的部分。
- `summarizer=None` 在 demo 中停用了會損失資訊的摘要。

### 如何接進現有架構

context management 在每次 model call 之前執行：

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)   # 8 · keep context under the window
    response = model(messages, registry)
    ...
```

本章動到的是 loop 本體。前幾章加的都是 tool 或 dispatch 行為，loop 本身不用改。但 context 縮減必須在每次 model call 之前跑，所以只能寫進 loop 裡。

loop 仍然維持同樣的不變條件：它用一個有效的 `messages[]` 呼叫模型，接著附上回應和任何 tool 結果。

### 對照：把 tool 輸出寫出去

Claude Code 和本章的 `_budget` 都是就地把過大的 tool 結果縮小，被切掉的那段就永遠沒了。

deepseek-harness 從不動已經發生的事。session log 只會被附加，模型看到的 messages 只是這份 log 的一個投影。
每次縮減都是再寫一則事件，說明要替換掉哪一段，所以 session 續跑或 fork 之後，重放出來的畫面一模一樣。

過大的 tool 輸出走的是另一條路，而且更早。結果一超過內嵌上限，tool 一回傳就直接送進 spill store。
store 把完整文字存起來，回傳一個位址。留在 context 裡的只有頭尾預覽、那個位址，以及一句提示：要完整內容就去讀或 grep 這個檔案。
所以輸出還在：模型需要剩下的部分時，自己去把檔案要回來。

[`src/spill.py`](src/spill.py) 就是這件事的精簡版。它是對照用的 demo，沒有接進 `manage()`，所以後面的章節照樣沿用同一套 pass。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book，也未經下面表格的系統證實。

**stub 每次都要是同一串字：**用來取代 tool 結果的那段文字也算在前綴裡，所以它每次都要一模一樣。
第一次替換時就把它定下來，之後照抄，連 session 從硬碟還原回來也照抄。
stub 如果重新算過，帶上新的時間戳或新的路徑，前綴就變了，它後面的 cache 也沒了。

**壓縮和 cache 想要的剛好相反：**壓縮要改寫歷史，cache 卻是歷史都不要動才划算。
歷史只要動過，改動點之後的 cache 就全部失效，下一次呼叫得重讀整段前綴。
每個 turn 都修一點，這筆帳就每個 turn 都要付。累積到 token 門檻再一次修完，只付一次。
不管走哪一種，壓縮都跑在兩次 API 呼叫之間，不能跑在一次呼叫裡面。

**這個 pass 也可以交給伺服器做：**Claude API 的 context editing 會把比較舊的 tool 結果從前綴裡拿掉，harness 這邊一行程式都不用寫。
但它一樣要重建一次 cache。所以它的位置在靠近 overflow 那一端，不是每個 turn 都用。

**摘要是寫給當前任務的，不是寫給整個 session：**把發生過的事情從頭複述一遍，並不是下一次呼叫需要的東西。
換個問法：下一次呼叫還缺什麼？照重要性由高到低，留這些：

- 已經定案的架構與設計決策。
- 新增或改過的檔案，以及改了什麼。
- 最近一次檢查或測試的成敗狀態。
- 還沒做完的 TODO，以及現在做到哪一步。

真要丟東西，先丟原始 tool 輸出。大的結果 budget pass 早就寫進硬碟了，真的需要再讀回來就好。

---

## 不同系統怎麼做

各 agent 如何決定要騰出空間，以及要移除什麼。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | 長 session 撐得下去，縮減成本低，存下來的輸出還能重讀。 | 沒有東西要調度、要調參，行為一眼就能看懂。 | 歷史從不被銷毀。 |
| **限制** | 各個 pass 要講究順序。摘要可能丟掉之後要用的細節。 | 歷史只會成長。run 拖得比預算久，window 塞爆就中止。 | log 在硬碟上只會長大，還得管鎖和 fold。 |
| **設計原因** | 互動式 session 沒有固定終點，window 遲早會滿。 | 假設預算會先讓 run 結束（見第 21 章）。 | log 才是事實，所以要縮的是投影，不是歷史。 |
| **做法：trigger** | token 門檻，外加 `prompt_too_long` 的後備。 | 每則 observation，在 render 時處理。 | 每一步都量一次壓力，加上確認過的 overflow。 |
| **做法：strategy** | 先跑低成本 reducer（存檔、清成 stub），最後才摘要。 | 過長的輸出只保留頭尾，沒有壓縮。 | 先寫出去、再修剪，最後一則摘要事件。 |
| **做法：budget** | 保留 output 和安全緩衝空間。 | 每則 observation 上限一萬字元。 | 依模型換算比例：0.8 觸發壓縮，保留 0.16。 |

---

## 常見問題

- **摘要漏掉需要的細節：**持久化完整輸出，並在需要時重新讀取檔案。
- **壓縮反覆失敗：**使用 retry 上限或斷路器。
- **單一巨大 turn 仍然 overflow：**對 `prompt_too_long` 做出反應，執行一次有界限的最後手段裁剪。
- **pass 順序錯誤而遺失資料：**在把舊結果 stub 化之前，先持久化大型結果。
- **拆散的 tool 配對：**不要把一個 `tool_use` 和它相配的 `tool_result` 拆開。
- **stub 文字每次都不一樣：**preview 若帶著新的時間戳或路徑重新產生，前綴就變了，cache 直接失效。stub 字串第一次生成後就固定下來。
- **每個 turn 都修一點：**每次改動都會讓改動點之後的 cache 失效，小修小補反而比一次修完貴。用門檻觸發，成批處理。
- **模型全盤相信摘要：**注入的狀態摘要，模型會當成事實讀，幾乎不會回頭查證。摘要裡留下指向原始檔案的線索，寫錯了才追得回來。

---

## 動手跑跑看

[`src/`](src/) 沿用 07 並加上：

- [`context.py`](src/context.py)：`budget`、`micro` 和 `auto` 這幾個 pass 都透過 `manage` 執行。
- [`loop.py`](src/loop.py)：在每個 turn 的最上方呼叫 `context.manage()`。
- [`spill.py`](src/spill.py)：deepseek-harness 的對照：過大的結果整份存起來，context 只留預覽和檔案路徑。
- [`test.py`](src/test.py)：獨立檢查每一個 pass，再加上一次 spill，確認完整文字還讀得回來。
- [`demo.py`](src/demo.py)：驅動已接上 context management 的 loop。

```bash
python sections/08-context-management/src/test.py         # offline checks, no key
uv run python sections/08-context-management/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 原始碼](https://github.com/yasasbanukaofficial/claude-code)：`services/compact/autoCompact.ts`、`microCompact.ts`、`timeBasedMCConfig.ts`、
  `compact.ts`、`utils/toolResultStorage.ts`、`query.ts`、`query/tokenBudget.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`config/mini.yaml` 的 observation template、`models/litellm_model.py` 的 `abort_exceptions`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/compaction/compaction/src/index.ts`、`packages/compaction/compaction-basic/README.md`、`packages/llm/token-meter/src/index.ts`、
  `packages/spill/spill/src/index.ts`、`packages/spill/spill-policy/README.md`、`docs/subsystems/compaction.md`、`docs/subsystems/session.md`。
- [learn-claude-code · s08_context_compact](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
- [ai-agent-book · 第 2 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md)（《深入理解 AI Agent》，李博杰，以中文原版為準）：
  context rot、in-context learning 其實是檢索、壓縮與 cache 的相互影響、針對任務的壓縮與保留優先序、
  API 層的 context editing、凍結的 tool 結果 stub，以及模型會把注入的摘要當成事實這個結論。
- [Lost in the Middle](https://arxiv.org/abs/2307.03172)（Liu 等人，TACL 2024）：放在長上下文中段的事實，取用準確度會掉下來。這是 context rot 的依據。

以下是推測，在上面那份 Claude Code 原始碼 repo 裡找不到完整實作：

- `snipCompact.ts`：只看得到 `snipCompactIfNeeded(messages)` 的呼叫點。
- `reactiveCompact.ts`：reactive 路徑看起來位於 `compact.ts` 中。
