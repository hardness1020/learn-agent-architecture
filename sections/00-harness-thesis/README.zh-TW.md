# 0 · Harness thesis

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 模型負責判斷，harness 負責讓判斷安全地變成行動。

模型負責推理、選擇工具，以及判斷何時停止。harness（外層架構）則是包在模型外面的程式碼，包括 loop、tool、memory、permission 和各種 interface。

單次模型呼叫只會根據輸入產生一個回應。模型可以判斷接下來該做什麼，卻無法自己執行。它沒有持久狀態、工具執行環境、檔案存取能力，也沒有權限關卡。

harness 必須：

1. 提供實際執行動作的環境。
2. 把有用的執行結果送回模型。
3. 在動作影響真實系統前先檢查風險。
4. 保存狀態，讓後續呼叫能接續先前的進度。

沒有 harness，模型就只能回答問題，無法執行工具、讀取結果，也無法在多次呼叫之間保留工作進度。

---

## 核心機制

![機制圖](assets/00-harness-thesis.png)

本章先釐清模型與 harness 的分工。模型呼叫位在核心，輸入由 harness 準備，輸出也由 harness 接手處理。

簡單來說，模型負責判斷，harness 負責環境與執行。

第 1 章的 loop 是核心控制流程。其他章節在它周圍加上輸入、檢查或狀態：

- 第 2 章加上 tool runtime 與 dispatch。
- 第 3 章加上 permission 與 sandbox。
- 第 4 章加上攔截生命週期事件的 hook。
- 第 8 章與第 9 章加上 context 管理與跨 session memory。
- 第 10 章在每一輪產生 system prompt。
- 後面的章節加上 task、background execution、scheduling 與 isolation。

這些部分不會取代 loop。它們把輸入送進 loop、為 loop 把關，或替 loop 保存狀態。

### Harness 不是越複雜越好

每一層 harness，都是在補當下模型做不到的事。這讓每一層都帶著兩個成本：

1. 程式碼變多，要維護的變多，會出 bug 的地方也變多。
2. 設計綁著某一代模型。新模型可能自己就會規劃、恢復、驗證，這時還硬套舊的補救方式，反而會拉低表現。

所以 harness engineering 不是只有加，也包含刪。模型換代時，重新評估每一層：還有幫助的留下，新模型自己就做得到的就刪掉。
怎麼量測，見第 20、21 章。mini-swe-agent 就是最極端的例子：幾乎沒有 harness，也就幾乎沒有東西需要重新評估。

deepseek-harness 是從另一端回答同一個問題。它每個部分都是 plugin，連 loop 也是，沒有哪一塊算是特權核心。
每種能力都有自己的擴充點，並由對應的 plugin 負責，例如工具、權限和 context 處理各自獨立。
所以重新評估某一層只是改設定，不用 fork。要刪掉一層，就是不要載入那個 plugin。

### 延伸閱讀

以下兩個框架出自 ai-agent-book，是可以拿去驗的說法，不是本專案的結論。

**界線怎麼驗：**模型和 harness 的界線落在哪，會隨著模型變強而移動。書上有兩個說法可以拿去驗：

- **scaffold 要多厚，看模型有多強：**弱一點的模型需要被逼著先規劃，需要重試階梯，需要寫死的檢查。強一點的模型自己就會做這些事。
- **什麼時候該動手，是模型的策略：**不用再讀、可以動手改的那個點，是模型訓練時學來的。prompt 裡寫一句話、把步數壓小，都只能推它一點，訂不了它。

**這兩個說法可以合成同一個測試：**同一套 scaffold 拿到兩代模型上跑，分數可能往相反方向走。
這件事只有書上一次實驗撐著，所以去驗方向，不要直接信幅度。
接著逐層檢查：這項能力應該由模型還是 harness 負責？如果模型已經能可靠完成，這一層就是多餘的，只是在浪費 token。

**Agent 先在哪裡跑得起來：**一個任務現在適不適合交給 agent，看兩件事：目標能講多精確，還有結果機器判不判得出來。
寫程式這兩項都很高。一張 ticket 或一個失敗的測試就把目標講清楚了，測試、型別、linter 和 git 會說什麼時候算做完。
這些基礎建設本來是人蓋給自己用的，agent 直接拿來當現成的驗證 harness。coding agent 最早成熟，原因就在這裡。

**少掉其中一項的話：**任務不是變得籠統地難，而是會用特定的方式壞掉，有兩種：

- **目標清楚，但機器驗不了：**例如把一頁文字改得更好讀。loop 沒有停止條件，沒人說不行，它就當作做完了。
- **機器驗得了，但目標講不清楚：**例如把一個模組整理乾淨。loop 會對著檢查做，它能證明沒有東西壞掉，但這不是你要的東西。

**這兩種要用不同的方式補：**領域裡本來就沒有檢查，第 21 章教你把檢查建起來。目標講不清楚的話，補再多檢查也沒用。

---

## 不同系統怎麼做

哪些事讓模型決定，哪些事交給周圍的程式碼。

| | Claude Code | mini-swe-agent |
| --- | --- | --- |
| **優點** | harness 帶來安全性、持久化、subagent，以及隨需載入的知識。 | 幾乎沒有 harness 程式碼，也就沒什麼要維護的。 |
| **限制** | 程式碼大多集中在 harness，要維護的東西多，bug 也大多出在這裡。 | 除了執行 bash 之外的每一種能力，都得靠模型自己。 |
| **設計原因** | 模型呼叫無法自行行動，所以環境全由 harness 負責。 | 假設一個 bash 工具就夠了。hook、skill、memory 與 task 都刻意不存在。 |
| **做法：model owns** | 判斷、選擇工具、決定停止。模型看得到工具名稱、schema 與結果。 | 判斷、怎麼改檔案、何時提交。 |
| **做法：harness owns** | loop、tool、permission、hook、knowledge、task 與 coordination。 | 一個 loop、一個 bash tool、跑指令前先問過使用者，再加上步數與成本上限。 |
| **做法：size signal** | 多數程式碼都落在模型呼叫之外。 | 整個 agent class 大約 150 行。 |

---

## 常見問題

- **把 harness 的行為歸功給模型：**權限檢查與錯誤復原是 harness 的行為。它們出錯時要修的是 harness。
- **把該由模型做的決定寫死：**僵硬的工具順序與寫死的規劃會和模型衝突。需要判斷時，就讓模型去決定。
- **harness 太少：**一個沒有工具、權限或 context 管理的 loop，會把模型停在聊天機器人的層次。補上缺少的那一層。
- **harness 太多：**每加一層就多一份要維護的程式碼，而且為舊模型設計的那一層，可能反過來拖住新模型。模型換代時重新評估，沒有幫助的就刪掉。
- **把模型的策略當成 harness 的設定：**什麼時候停止蒐集資訊，是模型學來的，不是設定出來的。prompt 規則只能推它一點。這一層要不要留，量過再決定。
- **在機器判不出結果的地方跑 agent：**loop 分不出這是做完了還是做錯了。補一個檢查器，或是讓人留在流程裡。
- **職責混在一起：**把權限邏輯塞進工具執行裡，會更難測試也更難替換。維持清楚的契約，例如 `Tool.ts` 與 `PreToolUse`。

---

## 參考資料

- [Claude Code source (`cc-src/src`)](https://github.com/yasasbanukaofficial/claude-code)：`QueryEngine.ts`、`query/`、`Tool.ts`、`tools/`、`hooks/`、`types/permissions.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py`、`environments/local.py`、`__init__.py` 裡的 protocol。
- [mini-swe-agent README](https://github.com/swe-agent/mini-swe-agent)：模型變強之後，harness 可以更小的理由。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/architecture.md`、`docs/capability-seams.md`、`docs/cordis-primer.md`、`docs/subsystems/core.md`。
- [learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter5.md`，以中文原版為準。界線框架與任務象限，兩者都只有單一來源。
