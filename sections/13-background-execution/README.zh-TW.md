# 13 · Background execution

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 把耗時工作移到背景執行，主 loop 可以先繼續處理其他事。

有些操作需要很長時間，例如安裝依賴、建置、執行完整測試、整理 memory，或啟動一個擁有自己 loop 的 subagent。

基本的 agent loop 會等工具呼叫完成，才進行下一次 model call。

這對快速讀取沒有問題，但有些工作跑得很久，讓整個 loop 原地等待就很浪費。這類工作其實可以自己在背景跑，agent 同時繼續處理其他事項。

background execution 必須：

1. 判斷哪些操作適合用非阻塞方式執行。
2. 啟動工作後立刻回傳 handle。
3. 追蹤 `running`、`completed`、`failed` 和 `killed` 等狀態。
4. 工作完成後，再把通知送回 loop。

少了這一層，一個耗時指令就可能卡住整個 agent。

---

## 核心機制

![機制圖](assets/13-background-execution.png)

背景執行由三個部分組成：

1. starter：把工作移出 loop，並回傳 handle。
2. runtime：持續追蹤 task 狀態。
3. queue：等工作完成後，在後續 turn 注入 notification。

loop 不會停下來等這件工作跑完。

- 背景執行是一個執行選項，而不是一種特殊的工具型別。
- 被背景化的呼叫會立刻回傳一個正常的 `tool_result`。
- 真正的結果稍後才會用另一則 notification 送進來。
- 一整個 subagent 也可以在背景執行。

### 本章新增：在 loop 外啟動工作，把 notification 收進對話

`start` 在一個 worker thread 上跑工作，並回傳一個 task id：

```python
def start(self, fn):                                   # src/background.py; returns immediately
    self._next += 1
    tid = self._next
    self._state[tid] = "running"
    def work():
        try:
            self._finish(tid, "completed", str(fn()))  # enqueues a <task_notification>
        except Exception as e:
            self._finish(tid, "failed", f"{type(e).__name__}: {e}")
    threading.Thread(target=work, daemon=True).start()
    return tid
```

`drain_into` 把已完成的 notification 併入下一個 user turn：

```python
def drain_into(messages, runtime):                     # src/background.py
    notes = runtime.drain() if runtime else []
    if notes and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = "\n".join(notes) + "\n\n" + messages[-1]["content"]
```

`backgroundable` 包裝任何工具，並在它的 schema 加上 `run_in_background`：

```python
def backgroundable(tool, runtime):                     # src/background.py; wraps ANY tool
    def run(a):
        if a.get("run_in_background"):
            inner = {k: v for k, v in a.items() if k != "run_in_background"}
            tid = runtime.start(lambda: tool.run(inner))
            return f"started background task {tid} ({tool.name}); ..."
        return tool.run(a)
    ...
    return replace(tool, run=run, ...)
```

這層包裝也決定了 model 會拿到什麼。丟到背景的呼叫只是把工作啟動起來：它回傳一個 task id，結果稍後才用自己的那則事件送回來。
跑很久的工具，名字和描述就照這樣寫（`initiate_export`，不要寫成 `export`）。model 才會把當下那則 `tool_result` 讀成收據，而不是答案。

### 如何接進現有架構

loop 在一個 turn 開始時，把 queue 裡累積的完成 notification 收進對話：

```python
background.drain_into(messages, runtime)               # src/loop.py
```

「一個工具呼叫對一個工具結果」的規則依然成立。一則遲來的完成 notification，不是給舊 `tool_use_id` 的延遲 `tool_result`。它是一則全新的 notification 訊息。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book，也未經下面表格的系統證實。

**打斷與安全點：**有些訊息不能等目前這個工具呼叫跑完。
使用者的修正、一個取消、一則警報，都可能在呼叫跑到一半時進來。一種做法是把所有進來的訊息都變成同一條 stream 上的 event。
loop 只在安全點（safe point）去讀這條 stream，也就是一則工具結果剛跑完、下一次 model 呼叫還沒發出的那個空檔。
呼叫跑到一半硬塞會弄壞對話紀錄，所以 event 得等那個空檔。

event 有多急，決定它要等哪一個空檔：

- **Queue：**等下一個空檔。完成通知和不急的訊息都走這條。
- **Cancel：**直接中止進行中的呼叫，當場空出一個空檔。適合那種再跑下去也白跑的修正。
- **Parallel：**丟到旁邊的 loop 去跑，主 loop 不動。

分類這件事本身不貴。用一個小 model 就能把 event 分成這三類，每則 event 只多花一次呼叫。

**打斷佔位：**取消完還要多做一步，對話紀錄才會是合法的。
被中止的那個呼叫留下一個 `tool_use` block，卻沒有對應的 `tool_result`，而下一次 model 呼叫需要這一對是完整的。
ai-agent-book 的做法是當場補：對同一個 id 補一則佔位用的 `tool_result`，內容寫這個呼叫被中斷了。
這跟上面那條不重用 id 的規則不衝突。佔位的那則當下把這一對收乾淨，真的結果照樣稍後用自己的 notification 送進來。
佔位這套是書作者自己提的設計，目前沒有第二個出處。

---

## 不同系統怎麼做

各個 agent 如何把工作移出 loop，又如何回報完成。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **優點** | 吞吐量提升，也不再有閒置的等待。單純的等待不會卡住任何東西。 | 同一個登記處管 shell、終端機和 child agent，收結果和喊停都走同一條路。 |
| **限制** | 結果可能較晚抵達，順序也可能顛倒。runtime 要顧狀態和清理。 | 叫醒閒著的 agent 會花掉模型輪次，所以得給它一個額度。 |
| **設計原因** | 一個跑很久的指令不該凍結整個 agent。 | 工作跑完要讓模型知道，而不是叫模型自己一直去問。 |
| **做法：off-loop primitive** | 背景 shell task 和背景 agent task，subprocess 繼續跑，輸出被轉導。 | 任何工具都能帶一個「丟到背景跑」的旗標，回傳一個 job id。 |
| **做法：notification** | 一則 `<task_notification>` 訊息，完成訊息走同一個共享 queue。 | 每個 job 一則通知。誰先結束誰算數，重複的會被壓下來。 |
| **做法：re-entry** | notification 在 turn 之間收進對話，分 `now`、`next`、`later` 三種優先級。 | agent 忙的話下一步就收到；閒著就叫醒它，次數有上限。 |

---

## 常見問題

- **互動式提示卡住（Interactive prompt stalls）：**某個背景指令在等輸入。偵測像提示的輸出，並通知 model 去 kill 它，或以非互動方式重跑。
- **完成訊息遺失（Lost completion）：**某個完成的 task 從沒抵達 loop。讓完成訊息走同一個共享 queue，並把 task 標記為已通知。
- **配對錯誤的 notification（Mispaired notification）：**重用舊的 `tool_use_id` 會弄壞 transcript。改用獨立的 notification 文字。
- **被 kill 之後的副作用（Side effect after a kill）：**timeout 或取消都不會告訴你那個呼叫到底做成了沒。盲目重試可能扣兩次款。先查狀態再寫入，或帶上 idempotency key。
- **批次 event 稀釋注意力（Batched events dilute attention）：**一次 drain 可能把好幾則 notification 併進同一個 turn，model 就只回應最後一則。幫每則 event 編號，再加一行摘要。
- **並行太多（Too much concurrency）：**太多背景 task 會耗盡資源。加上 kill 路徑和上限。
- **離場時的 process 洩漏（Process leak on exit）：**背景工作可能活得比 session 還久。註冊清理機制。

---

## 動手跑跑看

[`src/`](src/) 把 12 帶了過來，並加上：

- [`background.py`](src/background.py)：一個 runtime、notification queue、`drain_into`，以及 `backgroundable`。
- [`loop.py`](src/loop.py)：在呼叫 model 前，把待處理的 notification 收進對話。
- [`test.py`](src/test.py)：檢查 start、failure、drain，以及背景 subagent。
- [`demo.py`](src/demo.py)：在背景啟動一個 subagent，稍後再讀取它的結果。

```bash
python sections/13-background-execution/src/test.py         # offline checks, no key
uv run python sections/13-background-execution/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code task sources](https://github.com/yasasbanukaofficial/claude-code)：`tasks/LocalShellTask/`、`tasks/DreamTask/`。
- [Claude Code tool and queue sources](https://github.com/yasasbanukaofficial/claude-code)：
  `tools/BashTool/BashTool.tsx`、`tools/SleepTool/prompt.ts`、`utils/task/framework.ts`、`utils/messageQueueManager.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/jobs/jobs/src/index.ts`、`packages/jobs/jobs-local/src/index.ts`、`packages/jobs/tool-jobs/README.md`、
  `docs/subsystems/jobs.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s13_background_tasks](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter4.md`，以中文原版為準。
  冪等性與取消語義、啟動與完成分開命名、在安全點做 event 分類、打斷佔位、批次 event 的注意力稀釋。
