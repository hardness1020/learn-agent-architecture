# 11 · Error recovery

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> 先判斷錯誤類型，再決定要重試、調整，還是停止。

一次 agent 執行通常包含多次模型呼叫，其中任何一步都可能因網路問題、服務過載、rate limit、輸出上限或 context overflow 而失敗。

而且會出錯的不只有模型呼叫。有一份針對 production coding agent 的研究，把失敗分成四個層級：

- **API：**timeout、rate limit 和過載。
- **tool：**指令回傳非零，或是 handler 拋出例外。
- **context：**prompt overflow，或是 API 不收的訊息歷史。
- **control flow：**一直重複、卻走不到任何地方的步驟。

先確認錯誤發生在哪一層，再決定重試次數。若順序反過來，預算很容易浪費在重試也無法解決的問題上。

loop 對不同的失敗需要不同的回應：

1. 暫時性錯誤可以重試。
2. prompt 或輸出上限造成的問題，要先調整內容再重試。
3. 確定無法復原時，就停止。

沒有復原機制，一次短暫的 API 故障就可能讓執行已久的任務前功盡棄。

---

## 核心機制

![機制圖](assets/11-error-recovery.png)

最直接的做法，是用 retry helper 包住模型呼叫。它先判斷錯誤類型，再執行次數受限的復原策略。

- 暫時性的狀態碼會退避後重試。
- prompt overflow 會執行一次壓縮 callback，然後重試。
- 反覆的過載可以觸發 fallback model。
- 未知或不可重試的錯誤會被拋出。

### 本章新增：分類、backoff 與 retry helper

```python
RETRY_STATUS = {408, 409, 429}                         # src/recovery.py; these plus any 5xx

def should_retry(status) -> bool:
    return status in RETRY_STATUS or (status is not None and 500 <= status < 600)

def retry_delay(attempt, retry_after=None) -> float:   # exponential backoff + jitter
    if retry_after is not None:
        return float(retry_after)
    base = min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)
    return base + base * 0.25 * random()
```

overflow 會在一般狀態處理之前先檢查。如果壓縮能縮小 prompt，`prompt_too_long` 錯誤就是可復原的。

```python
def _status(e):
    return getattr(e, "status_code", None)

def _is_overflow(e) -> bool:
    return getattr(e, "overflow", False) or "prompt is too long" in str(e).lower()
```

`with_retry` 持有每次嘗試的狀態：

```python
def with_retry(call, on_overflow=None, fallback_model=None,
               max_retries=DEFAULT_MAX_RETRIES, sleep=time.sleep):
    consecutive_529 = 0
    overflowed = False
    for attempt in range(1, max_retries + 2):
        try:
            return call()
        except Exception as e:
            if _is_overflow(e):
                if on_overflow is None or overflowed:
                    raise
                overflowed = True
                on_overflow()
                continue
            status = _status(e)
            if status is None:
                raise
            if status == 529:
                consecutive_529 += 1
                if fallback_model and consecutive_529 >= MAX_529_RETRIES:
                    raise FallbackTriggered(fallback_model)
            if attempt > max_retries or not should_retry(status):
                raise
            sleep(retry_delay(attempt, getattr(e, "retry_after", None)))
```

### 如何接進現有架構

loop 把它的模型呼叫包起來：

```python
response = recovery.with_retry(
    lambda: model(messages, registry, system),
    on_overflow=lambda: _reactive_trim(messages),
    fallback_model=fallback_model)
```

- Recovery 只包住模型呼叫。
- `_reactive_trim` 就地修改 `messages[]`，供一次 overflow 重試使用。
- 當 recovery 放棄時，錯誤會被浮現出來，而不是被藏起來。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book，也未經下面表格的系統證實。

**怎麼抓到不會拋出例外的 loop：**假設 agent 跑了一次測試、讀到同一個錯誤，然後又把同一份測試跑一次。
沒有東西會拋出例外，所以重試路徑一條也不會啟動，任何界限也都碰不到。這是 control flow 層的失敗，得自己配一套偵測。

偵測靠的是 fingerprint：tool 名稱加上參數。同一個 fingerprint 又出現，就是 agent 在重做同一次呼叫。
步數上限的確會結束這次執行，但那時整份預算都花光了。fingerprint 計數器幾步之內就能結束，而且說得出是哪一次呼叫卡住。

復原路徑也各自需要計數器。每條路各數各的失敗次數，一直失敗的那條就會自己跳閘，不用等到全域上限。

**連上了卻沒聲音的 stream 怎麼收掉：**stream 可能接得上、吐了幾個 token，然後就停住。
那個時候 connect timeout 早就過了，所以什麼都不會觸發，loop 就一直等下去。

解法是再加一個計時器。在 connect timeout 旁邊放一個 idle watchdog，時間窗內沒有 token 進來就取消這次呼叫。
接著 retry helper 會把這次取消當成一般的暫時性失敗來處理。

**訊息歷史壞掉之後怎麼補：**一輪中途 crash，可能留下一個沒有對應 `tool_result` 的 `tool_use` block。
下一次請求會卡在訊息格式，而不是卡在工作本身，而且成對關係沒修好之前，它會一直卡在那裡。

至於「修」是什麼意思，得看這份 transcript 是拿來做什麼的。答案有兩種：

- **產品用的 harness 會修：**塞一個 placeholder 結果，寫明這次呼叫被中斷，執行就能繼續。
- **錄訓練資料的 harness 不修：**編一個結果出來，等於教模型一個根本沒發生過的步驟。

**失敗要讓呼叫端看到多少：**復原不是一個決定。要照失敗該有多明顯來分級：

1. **安靜重試：**呼叫端只看得到最後的結果。
2. **降級後繼續：**回傳一份縮水的結果，並說清楚少了什麼。
3. **把失敗浮現出來：**列出試過哪些方法，讓模型可以換一條路走。

前兩級產生的錯誤需要隔離。先留在 helper 裡面，等復原真的放棄了才放出去。
太早傳到模型面前的錯誤會被當成最終結果，模型可能因此重做一件其實已經成功的工作。

**別讓復原自己餵自己：**錯誤路徑上可能會觸發 hook、摘要或通知。
那些事情又去呼叫一次模型，然後又失敗一次，這次失敗又把同一條路徑再觸發一次。

有兩條規則可以把這串斷開。第一，在錯誤路徑上把帶副作用的邏輯關掉。第二，帶一個遞迴深度計數器收拾漏網的。
背景呼叫則完全不重試：它們不在關鍵路徑上，重試只會把主 loop 需要的額度花掉。

**這些界限是怎麼訂出來的：**這一節每個界限都是有人挑出來的數字：重試幾次、失敗幾次就停、idle 的時間窗多長。
每一個都要照實際量到的失敗來挑，不是靠直覺。書裡「壓縮試三次就停」這個界限，就是從生產環境反覆復原失敗的數據裡得出來的。

---

## 不同系統怎麼做

Recovery 包住模型呼叫。loop 主體維持不變。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | 針對性的復原路徑，救回的 run 比一概重試更多。 | 只有三條路徑要維護，crash 也留得下完整軌跡。 | 每次重試都寫進 log，session 續跑後也知道自己重試過什麼。 |
| **限制** | 要維護的分支與界限更多。 | 救回的 run 較少。overflow 會中止，連續三次格式錯誤也會。 | 沒有 fallback 模型。always 模式會無上限地一直重試。 |
| **設計原因** | 一次暫時的 API 失敗不該終結長任務。 | 重試、把格式錯誤還給模型，其餘具名退出。 | log 才是事實，所以復原是重開一個 turn 重放。 |
| **做法：retry** | 帶退避重試 429、408、409 和 5xx，`retry-after` 優先。 | tenacity 退避 4 到 60 秒，最多 10 次。 | 失敗的 turn 收掉後發一則錯誤事件，接著開新的 turn。 |
| **做法：token handling** | 提高輸出上限、在 `max_tokens` 停止後續寫，或壓縮。 | 沒有，overflow 直接中止 run。 | 一個統一的 overflow 代碼，先修剪再摘要。 |
| **做法：model fallback** | 反覆過載（529）後改用 fallback 模型。 | 沒有。 | 沒有。重試的 turn 會重建同一個請求。 |

---

## 常見問題

- **Retry storm：**許多 client 同時對過載重試會讓負載更糟。限制重試次數並尊重 `retry-after`。
- **無限復原：**提高上限、續寫和壓縮都可能無限 loop。為每條路徑設界限。
- **overflow 無法縮小：**如果一次 reactive compaction 失敗，就停止，而不是永無止境地壓縮。
- **錯誤消失：**一個被吞掉的錯誤會讓 transcript 少了結果。在復原用盡之後，把失敗浮現出來。
- **Stop hook 重播 API 錯誤：**對 API 錯誤訊息略過 stop hook。
- **卡住了，卻沒有錯誤：**一直重複的呼叫不會拋出任何東西，重試路徑一條也不會啟動。數重複的 tool 加參數 fingerprint，把這次執行停掉。
- **stream 靜靜停住：**stream 可能接上之後就沒聲音。這時 connect timeout 早就過了，什麼都不會觸發。加一個 idle watchdog。
- **修補污染了紀錄：**塞一個 placeholder `tool_result`，產品環境的執行是活下去了，但也記下了一個根本沒跑過的步驟。transcript 要留著當訓練資料，就別修。
- **中間錯誤外洩：**復原還沒結束就送出去的錯誤，會被當成最終結果，模型就白做一輪工。先留在 helper 裡，等復原放棄了再放出去。

---

## 動手跑跑看

[`src/`](src/) 承接 10 並加入：

- [`recovery.py`](src/recovery.py)：重試分類、退避、overflow 處理，以及 fallback 觸發。
- [`loop.py`](src/loop.py)：把它的模型呼叫包在 `with_retry` 裡。
- [`test.py`](src/test.py)：用一個假的不穩定呼叫驅動每一條路徑。
- [`demo.py`](src/demo.py)：在一次 live 執行中注入一次模擬過載。

```bash
python sections/11-error-recovery/src/test.py         # offline checks, no key
uv run python sections/11-error-recovery/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 原始碼](https://github.com/yasasbanukaofficial/claude-code)：
  `services/api/withRetry.ts`、`query.ts`、`services/api/claude.ts`、`services/api/errors.ts`、`query/tokenBudget.ts`、`utils/context.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：
  `models/utils/retry.py`、`models/litellm_model.py`、`agents/default.py` 的 `run()` 與 `max_consecutive_format_errors`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/llm/llm-retry/README.md`、`packages/llm/llm-retry/src/types.ts`、`packages/core/agent-loop/src/agent.ts`、
  `docs/subsystems/llm-streaming.md`、`docs/subsystems/core.md`、`docs/subsystems/persistence.md`。
- [ai-agent-book · 第 5 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter5.md)（《深入理解 AI Agent》，李博杰，以中文原版為準）：
  四層失敗分類、用 tool 加參數做 loop fingerprint、idle watchdog、`tool_result` 成對修補以及產品與訓練資料兩套標準、
  分級復原加錯誤隔離，還有防死亡螺旋的那幾招。它的註腳 ch5-3 說這套分類來自對生產環境 agent 的研究（其中包含 Claude Code），
  也提醒實作變動很快。書裡「壓縮試三次就停」這個界限，同樣是從量到的生產環境失敗裡訂出來的。
- [learn-claude-code · s11_error_recovery](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
