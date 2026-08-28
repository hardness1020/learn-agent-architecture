# 3 · Permission & sandbox

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 每個動作真正影響系統前，都必須先通過檢查。

模型可以要求使用任何已啟用的工具，permission 層則負責判斷這次呼叫能不能真的執行。

沒有 permission gate 的工具執行環境，幾乎等同於一個沒人看管的遠端 shell。

一次錯誤的工具呼叫就可能刪除檔案、洩漏機密，或推送錯誤的程式碼。單靠信任模型不能構成安全邊界，程式必須在執行前檢查每個請求。

原因很簡單：模型讀到的內容不一定是你寫的。網頁、issue 留言或 repo 裡的檔案，都可能暗藏操控 agent 的指令。
風險取決於三項能力：agent 能讀取私密資料、會接觸不可信內容，而且能把資料送到外部。
只有其中兩項時，風險還能控制；三項同時出現，惡意內容就可能誘使 agent 讀取機密並外傳。這個組合稱為 lethal trifecta。

持久化 memory 會進一步放大風險。惡意指令一旦被寫進 memory 檔案（第 9 章），下一次 session 就會把它讀回來，讓一次注入延續到原本對話結束之後。

permission gate 無法直接拿掉這三種能力，否則 agent 也無法完成工作。它改用兩層保護：會湊齊這三項能力的呼叫，前面先擺一道決策；已經放行的呼叫，後面再用 sandbox 限制影響範圍。

permission 層必須做到：

1. 在每個工具呼叫執行前先檢視它。
2. 決定 `allow`、`ask` 或 `deny`。
3. 當高風險的呼叫尚未預先核准時，詢問使用者。
4. 當呼叫真的執行時，限制它造成的損害。

沒有這一層，一次錯誤的工具呼叫就可能造成無法回復的後果。

---

## 核心機制

![機制圖](assets/03-permission-and-sandbox.png)

一個純函式負責做出 permission 決策。它讀取工具、目前的 mode，以及所有的 allow 規則，並回傳三個值之一：

- `allow`：執行工具。
- `ask`：暫停並詢問使用者。
- `deny`：不執行工具。

mode 會改變預設行為。舉例來說，plan mode 允許唯讀工具，但在計畫核准前拒絕編輯。

### 本章新增：permission gate

`decide()` 就是整個 permission 決策：

```python
def decide(tool, mode, allow_rules) -> str:      # src/permissions.py (new)
    if mode == BYPASS:                            # operator opted out
        return "allow"
    if mode == PLAN:                              # exploring, not acting yet
        if tool.is_read_only:           return "allow"
        if tool.name == "ExitPlanMode": return "ask"     # approval handshake (section 5)
        return "deny"                             # no side effects until approved
    if tool.is_read_only or tool.name in allow_rules:
        return "allow"
    if mode == ACCEPT_EDITS and tool.is_edit:
        return "allow"                            # a class of work pre-approved
    return "ask"                                  # default: when unsure, ask
```

這個函式沒有 I/O。這讓它可以一個 mode 一個 mode 地輕鬆測試。

### 如何接進現有架構

gate 在 `_dispatch` 內部執行，就在 `run_tool` 之前：

```python
def _dispatch(block, registry, mode, allow_rules, approver):   # src/loop.py
    ...                                                  # resolve tool (section 2)
    decision = decide(tool, mode, allow_rules)           # 3 · the gate, the new line
    if decision == "deny":
        return res(f"{name} not allowed in {mode} mode")
    if decision == "ask" and not approver(name, block.input):
        return res(f"{name} denied by user")
    return res(run_tool(tool, block.input))              # only now does it run
```

- loop 主體和第 1、2 章相同，沒有改變。
- 只有 `_dispatch` 多了 gate。
- `deny` 以及未核准的 `ask` 永遠不會抵達 `run_tool`。
- 拒絕結果仍會以 `tool_result` 回傳，所以模型看得到發生了什麼，並能隨之調整。
- `approver` 預設為 `False`，所以 `ask` 代表「否」，除非使用者核准。

關鍵不變條件維持不變：每個工具呼叫都會產生一則結果訊息，即使真正的動作沒有執行。

真實系統會加上規則優先序、記住的核准，以及沙箱化的執行。這些都是同一個 gate 的延伸。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book，也未經下面表格的系統證實。

**先看懂指令，不要比對字串：**agent 要求跑一條 shell 指令。`decide()` 只看得到一個工具名稱，所以真正要判斷的是那串指令。
一般的做法是拉一份字串 deny 清單，而這招會失敗。`rm -rf /` 很好抓，下面這幾條會過：

- `find . -exec rm {} \;` 把刪除塞在 flag 裡面。
- `$(echo rm) -rf /` 是 shell 跑的時候才把 `rm` 這個字拼出來。
- `curl -o /etc/crontab` 寫了一個檔案，卻沒用到任何一個寫入指令。

解法是換一個解析器，讓它讀結構而不是讀字面。它先把指令拆成程式和參數，也知道哪些 flag 後面要吃掉一個值，所以分得出參數和 flag。
接著它問每個程式會做什麼。`-exec` 自己帶一條指令，那條指令也要一起檢查。`-o` 指的是要寫入的檔案，那個路徑就當成一次寫入來檢查。

代價是解析器得為每個程式準備一套規則。它不認識的程式就讀不懂，所以沙箱還是要擋在它後面。

**結果對了，過程也可能要擋：**一張壞掉的資料表有兩種修法，最後都會得到一張好的資料表：一種是做 migration，
另一種是把它砍掉整個重建。結果檢查（第 21 章）兩種都會放行，因為它只看終態。

解法是連路線一起管，不是只管終點。就算重建出來的資料表是對的，砍掉重建這個動作照樣要擋。
代價是真的該重建的時候，也得找人來核准。

**沙箱擋掉哪些東西：**gate 也會判斷錯。沙箱的作用，就是讓判斷錯的那次 `allow` 不要付出太大代價。有三個限制做掉大部分的工。

- **Egress：**網路預設擋掉，放行的流量走一個握有 host 允許清單的 proxy。
  三隻腳裡，這一隻砍起來最便宜。agent 照樣讀程式碼、照樣寫檔案，只是哪裡都送不出去。
- **Mount：**原始碼用唯讀掛載。憑證檔案一個都不要掛。只給一個可寫的工作目錄，其他都不給。
  agent 打不開的檔案，就外洩不出去。
- **Quota：**CPU、記憶體、磁碟、wall clock 時間都設上限。踩到上限的時候，回一個錯誤當 tool result，
  不要無聲把 process 殺掉。模型讀得到 timeout，就知道換一條短一點的指令。無聲殺掉，它什麼都讀不到。

**要問使用者，但不要讓他等兩次：**gate 回傳 `ask`，使用者現在就在等。如果檢查本身也慢，那在提示框出現以前，他已經先等過一次了。
推測式檢查把前面那一次等待拿掉。順序是這樣：

- harness 把 permission 檢查丟到背景跑。
- 畫面上馬上先顯示一行進度。那行進度不會改動系統上的任何東西。
- 如果那行還在顯示的時候檢查就回了 `allow`，工具直接執行，提示框不會出現。
- 如果檢查還沒定案，那行進度就換成確認提示。

這樣為什麼還是安全的：提早跑的只有檢查，工具本身還是要等檢查給答案。

---

## 不同系統怎麼做

各個 agent 如何管制副作用、切換 mode，以及記住決策。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | mode、有序規則與沙箱化提供精確的控制。 | 幾分鐘就能稽核完。拒絕會落回對話，模型讀得到原因。 | 拒絕只會收緊，沙箱判定 fail closed。 |
| **限制** | 要推敲的狀態很多。bypass 和預先核准的路徑都必須保持狹窄。 | 對每條指令一視同仁，而且什麼都不記。 | 政策分散在 guard、approval、沙箱和 preset 之間。 |
| **設計原因** | 每次呼叫都問會造成核准疲勞，所以系統會把核准記下來。 | 損害交給環境去限制，一個確認提示加一份 regex 清單就夠了。 | 每個關注點都是自己獨立的 fail-closed 服務。 |
| **做法：gate point** | 每個工具執行前。Web、MCP 與遠端執行各有核准路徑。 | 每一步的指令執行前。按 Enter 就核准，留言就是拒絕。 | 先跑 pre-execute 事件，再跑只會拒絕的 guard。 |
| **做法：permission modes** | Default、edit-approved、plan、deny 與 bypass。 | `human`、`confirm` 與 `yolo`，執行期可以切換。 | 沙箱 mode 加上 ask 或 never，打包成 preset。 |
| **做法：sandbox** | Bash 可以在沙箱內執行。 | 環境 class 就是沙箱：主機本身、容器，或包住執行。 | provider 逐次把 argv 包起來，拒絕會分類好讀回來。 |
| **做法：rule persistence** | 規則依優先序合併，可存到 session 或 settings。 | 白名單 regex 只寫在 config，符合的指令跳過確認。 | 旋鈕變動是 log 事件，重放折疊出政策。 |

---

## 常見問題

- **Pattern-match bypass：**字串式的 deny 清單會漏掉 shell 的各種變體。先把指令解析出來，看它實際會做什麼，再讓沙箱擋在解析器後面。
- **Mode 開得太寬：**一條範圍過大的 allow 規則或 bypass mode，可能讓後續的高風險呼叫悄悄執行。限縮 bypass 的範圍，並讓目前的 mode 顯示出來。
- **核准疲勞：**每次呼叫都詢問，會訓練使用者不看內容就核准。預先核准低風險的類別，但讓破壞性動作維持明確詢問。
- **subagent 內的無聲拒絕：**子 agent 可能沒有終端機可以詢問。應把提示往上轉給父 agent 代問，而不是無聲失敗。
- **沙箱被停用：**若一個被允許的指令在沙箱外執行，permission 提示就是最後一道檢查。任何未沙箱化的路徑都要用政策擋在後面。
- **被核准的呼叫照樣外洩：**每一次呼叫單獨看都過得了 gate，整個 session 合起來卻還是讀到機密又把它送出去。
  網路預設就擋掉，第三種能力根本沒得用。
- **驗得過但很破壞：**砍掉重建也能通過結果檢查，因為終態是對的。要檢查的是動作，不是只有終態。
- **memory 被下毒：**注入到 memory 檔案裡的指令，之後每一次 session 都會被讀回來。把存下來的 memory 當成不可信的內容，絕不當成操作者的規則。

---

## 動手跑跑看

[`src/`](src/) 承接 02 並加上：

- [`permissions.py`](src/permissions.py)：涵蓋四種 mode 的 `decide`。
- [`loop.py`](src/loop.py)：在 `_dispatch` 中於執行前管制每個呼叫。

```bash
python sections/03-permission-sandbox/src/test.py         # offline checks, no key
uv run python sections/03-permission-sandbox/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 原始碼](https://github.com/yasasbanukaofficial/claude-code)：`QueryEngine.ts`、`hooks/useCanUseTool.tsx`、`types/permissions.ts`、`utils/permissions/PermissionUpdate.ts`。
- [Claude Code 沙箱與 web gate](https://github.com/yasasbanukaofficial/claude-code)：`tools/BashTool/shouldUseSandbox.ts`、`tools/WebFetchTool/preapproved.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/interactive.py`、`environments/docker.py`、`environments/extra/bubblewrap.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/tools.md`、`docs/subsystems/approval.md`、`docs/subsystems/sandbox.md`、`docs/subsystems/permission-presets.md`、
  `packages/sandbox/sandbox-local/README.md`、`packages/shell/bash-sandbox/README.md`。
- [ai-agent-book · 第 5 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md)（《深入理解 AI Agent》，李博杰，以中文原版為準）：
  memory 把攻擊放大的那個維度、沙箱的 egress 與 mount、quota 政策、語意式的指令解析、推測式 permission 檢查，
  以及管路徑而不是只管結果。這幾項設計只有這一個來源。
- [The lethal trifecta for AI agents](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)（Simon Willison）：
  私密資料、不可信內容、對外通訊，這三種能力不能湊在一起。
- [learn-claude-code · s03_permission](https://github.com/shareAI-lab/learn-claude-code)：section framing。
