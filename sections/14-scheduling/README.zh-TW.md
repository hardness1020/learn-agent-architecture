# 14 · Scheduling

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> 讓時間也能啟動 agent，不必每次都等使用者輸入。

背景工作仍然需要觸發來源。許多 task 應該延後執行或定期重複，例如產生報告、發送提醒，或固定檢查某個狀態。

scheduling 的核心很簡單：先記下何時要做什麼；時間一到（fire），就把 prompt 放進 queue。原本的 loop 再把它當成新的 turn 處理。

排程必須：

1. 把 schedule 保存到單一 turn 之外。
2. 由獨立元件監看時間，不阻塞 loop。
3. schedule fire 時，把對應 prompt 放進 queue。
4. 視需求讓 schedule 在重啟後仍然存在。

少了這一層，agent 只能被動回應 user 輸入，無法在指定時間主動開始工作。

---

## 核心機制

![機制圖](assets/14-scheduling.png)

關鍵是把時鐘和 loop 分開。scheduler 只負責監看時間，不會直接呼叫 model。

時間到時，scheduler 只會把 prompt 放進 queue。driver 會等目前的 turn 結束，再取出 prompt，交給原本處理 user 輸入的 agent loop，當成新一輪執行。

- 一個 schedule 就是資料：要跑的 prompt、一個 fire 時間，以及選擇性的重複間隔。scheduler 把每一筆存成一個 task。
- 一次性（one-shot）的 schedule fire 一次後就把自己刪掉。
- 週期性（recurring）的 schedule 會重新裝填到下一個間隔。
- 一個 durable 的 schedule 能在重啟後存活，但在 host 關機時它不會 fire。
- heartbeat 是一種週期性 schedule，它問的是一個問題。它醒來、看一眼來源，多數時候判斷沒什麼好講的。

### 本章新增：scheduler 與 fire queue

`tick` 檢查哪些 task 已經到了預定時間。fire 就是把一個 prompt 放進 queue：

```python
def tick(self):                                       # src/scheduler.py; called by a daemon thread
    now = self._clock()
    for tid, t in list(self._tasks.items()):
        if now >= t["due"]:
            self._pending.put({"prompt": t["prompt"], "channel": t.get("channel")})
            if t["every"]:                            # enqueue, do not run the model here
                t["due"] = now + t["every"]
            else:
                self._tasks.pop(tid, None)
    self._save()                                      # durable tasks only
```

- 時鐘是可注入的，所以測試會用一個假時鐘。
- `run()` 在一個 daemon thread 上呼叫 `tick`。
- `_save` 把 durable task 持久化成 JSON。
- 在相同路徑上建立一個新的 `Scheduler`，會重新載入 durable task 並接續 id。

### 本章新增：投遞答案

fire 出來的那次執行沒有人在等，所以答案需要一條送出去的路。每個 task 可以指定一個 channel。
channel 就存在 task 裡，是那筆排程資料的一個欄位：`create(..., channel="console")` 存進去，`tick` fire 時再把它和 prompt 一起放進 queue。
所以 driver 從 queue 拿出來的每個項目，已經是 `{"prompt": ..., "channel": ...}`，不用再去別處查這個答案要送哪。

`deliver` 負責把這個 turn 的答案送到 channel（Hermes 會把 cron 輸出投遞到該 job 的聊天平台）：

```python
SILENT = "[SILENT]"                              # a fired run may decide nothing is worth sending

def deliver(channels, fired, text) -> bool:      # src/scheduler.py
    if not fired.get("channel") or text.lstrip().startswith(SILENT):
        return False
    channels[fired["channel"]](text)
    return True
```

- `channels` 把 channel 名稱對應到一個送信的 callable（這裡是 print；真正的 adapter 是第 19 章的事）。
  task 指定 channel；driver 擁有這張對照表。兩邊互不知道對方的細節。
- 答案以 `[SILENT]` 開頭時，`deliver` 直接跳過，不把它送進 channel。這是排程檢查的約定：這次跑完沒發現任何值得通知使用者的事（例如輪詢沒看到變化）。driver 手上仍有完整文字，可以寫進 log。
- 沒有 channel 表示答案留在本地，也就是加入投遞之前的行為。
- `bool` 回傳值讓 driver 可以改走別條路（demo 會印出未投遞的答案），而不是無聲地丟掉答案。

### Heartbeat

有些來源不會主動推播：沒有 webhook 的信箱、沒有 feed 的網頁、你不問就不回答的服務。
對這些來源，能用的觸發條件只剩時鐘。做法叫 heartbeat：一個週期性的 schedule，prompt 是叫 agent 去看一眼，不是叫它動手。
看一下來源，判斷有沒有變化值得講一句，沒有就不出聲。

heartbeat 跑完發現沒什麼好講的，就回一個 `[SILENT]`。照上面那條規則，`deliver` 什麼都不會送出去。
這一次 tick 只花一次 model 呼叫，channel 上不會多一則訊息，所以這個 schedule 可以跑得比較密。

heartbeat 和 cron 在這裡用的是同一組零件：一個 prompt、一個重複間隔、一個 channel。差別只在 prompt。
cron 的 prompt 是下命令，heartbeat 的 prompt 是問問題。

### 如何接進現有架構

排程分成兩半。`tick` 在自己的 daemon thread 上跑（第 13 章的背景執行），它不碰 model，fire 時只把 prompt 放進 queue：

```python
def run(self):                                        # src/scheduler.py; started by sched.run()
    def loop():
        while not self._stop.wait(self.CHECK_INTERVAL):   # wakes once per second
            self.tick()
    threading.Thread(target=loop, daemon=True).start()    # daemon: never keeps the process alive
```

真正執行 turn 的是前景的 driver：它在兩個 turn 之間把 queue 清空，替每個 fire 出來的 task 呼叫一次 `run_turn`：

```python
for task in sched.drain():                            # src/demo.py · between turns
    messages = [{"role": "user", "content": task["prompt"]}]
    deliver(channels, task, run_turn(messages, model, reg, session))
```

一個 fire 出來的 prompt 會變成一個新的、類似 user 的 turn。它用的是同一套 loop、權限、hook、記憶、context 管理和復原路徑。它的答案會送到該 task 的 channel。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book，也未經下面表格的系統證實。

**時鐘做得到的極限：**heartbeat 只有一個參數要調，就是間隔。它同時決定了帳單和最糟情況下的延遲，這兩件事會互相拉扯。
間隔短，model 一直醒過來，多半什麼也沒發現。間隔長，便宜，但消息晚。
換哪個間隔都解不掉。時鐘是在取樣狀態，不是在盯著事件，所以它只知道自己上次是什麼時候看的，不知道事情是什麼時候發生的。

**能用推播就用推播：**來源如果能主動呼叫 agent，事情發生的當下就觸發，輪詢成本歸零。
所以順序是：來源支援推播就用推播，不支援才用 heartbeat，真的跟時間綁在一起的工作（例如週一的報表）才用 cron。
入站推播那一側由第 19 章負責。

---

## 不同系統怎麼做

各個 agent 如何決定何時執行排程工作。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | 簡單又私密。durable 的 schedule 能在重啟後存活。 | 不需要託管服務，無人看管也能 fire。 | 提醒跟著 session 一起重放。錯過的幾次會併成一個 turn。 |
| **限制** | 只在 session 運行時才會 tick，remote trigger 還要託管服務。 | 需要一個 gateway，還要用鎖擋掉重複 fire。 | 只能固定間隔。session 關掉就什麼都不會 fire。 |
| **設計原因** | 假設本地有 session 開著。 | gateway 是 server process，無人看管也能 fire。 | 提醒就是對話狀態，所以歸 session log 管。 |
| **做法：trigger** | Cron、sleep 和 remote trigger，由 ticker 定期檢查。 | gateway tick 上的 cron，跟著使用者的時區走。 | 延遲多久後、某個時間點，或固定間隔，最快五分鐘一次。 |
| **做法：durability** | session 狀態，或存成一個帶鎖的 JSON 檔。 | 共享一個 JSON job store，認領是原子的。 | 寫進 session log 的事件。fork 保留歷史，但不帶走提醒。 |
| **做法：wakeup** | fire 出來的 prompt 進 queue，在 turn 之間執行。 | 到點的 job 平行跑，輸出投遞到聊天平台。 | 等 agent 完全閒下來，才排一個 turn。至少送達一次。 |

---

## 常見問題

- **重複 fire（Double fire）：**一次很快的 tick 可能在同一個 cron 分鐘內比對到不只一次。追蹤上一次 fire 的分鐘。
- **許多 schedule 一起 fire：**給週期性 task 加上決定性的 jitter，把觸發時間錯開。
- **durable 不等於永遠開機：**本地 durable schedule 只能在重啟後存活。要離線 fire，改用 remote trigger 或 OS timer。
- **cron 表達式有誤（Bad cron expression）：**在 create 時驗證，並跳過無效的已載入項目。
- **loop 正忙：**把 prompt 放進 queue，等 turn 之間再拿出來跑。
- **通知疲乏（Alert fatigue）：**heartbeat 每次 tick 都回報，使用者就學會忽略它。讓 prompt 自己判斷什麼值得送出，其餘時候就不出聲。
- **兩次 tick 之間的事件：**時鐘取樣的是狀態。在兩次 tick 之間出現又消失的變化，它看不到。改讀 log 或游標，或把來源換成推播。

---

## 動手跑跑看

[`src/`](src/) 把 13 帶了過來，並加上：

- [`scheduler.py`](src/scheduler.py)：一個 scheduler、fire queue、週期性重新裝填、一次性刪除、durable 的 JSON store，以及 channel 投遞（`deliver`、`SILENT`）。
- [`test.py`](src/test.py)：用一個假時鐘測試一次性、週期性、重新載入和投遞的行為。
- [`demo.py`](src/demo.py)：把一個 prompt 排在一秒後、以一個新 turn 執行它，並把答案投遞到 console channel。

loop 沒有改變。排程從 loop 之外啟動 turn。

```bash
python sections/14-scheduling/src/test.py         # offline checks, no key
uv run python sections/14-scheduling/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code)：
  `tools/ScheduleCronTool/`、`tools/RemoteTriggerTool/`、`tools/SleepTool/`、`utils/cronScheduler.ts`、`hooks/useScheduledTasks.ts`、`utils/queueProcessor.ts`。
- [Hermes Agent 原始碼](https://github.com/NousResearch/hermes-agent)：
  `cron/scheduler.py`（`tick`、`_resolve_cron_disabled_toolsets`）、`cron/jobs.py`（`_jobs_lock`、`claim_dispatch`）、`hermes_time.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/schedule/schedule/src/runtime.ts`、`packages/schedule/schedule/src/persistence.ts`、`packages/schedule/schedule/src/tools.ts`、
  `docs/subsystems/schedule.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s14_cron_scheduler](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter4.md`，以中文原版為準。
  帶判斷的 heartbeat 喚醒、通知疲乏，以及時間驅動觸發的極限。
