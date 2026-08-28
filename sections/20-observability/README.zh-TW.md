# 20 · Observability & evaluation

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 沒有完整紀錄，就無法重現問題、控制成本，也無法可靠評估 agent。

agent 會在無人看管的情況下持續執行、產生副作用，並消耗成本。單看一次模型呼叫，就像面對黑盒子：token 花掉了，真實動作也發生了，卻不知道中間經過什麼。

沒有 instrumentation，連最基本的問題都無法回答：它做了什麼？某個工具失敗幾次？這個 session 花了多少錢？

本章專注在記錄執行過程，把每一步做了什麼、花了多少成本整理成可保存、可查詢的資料。

至於某次修改究竟讓品質變好還是變差，會在第 23 章處理；評估所需的素材，正是本章收集的紀錄。

如果沒有這些紀錄，成本突然上升時找不到原因，bug 無法重現，eval dataset 也缺少真實案例可用。

---

## 核心機制

![機制圖](assets/20-observability.png)

這裡有兩條彼此獨立的 pipeline，而且都不改變 loop 的控制流程。

telemetry 會在 loop 的每一步呼叫 logger，送出事件後不等待結果，也就是 fire and forget。

event 的接收端稱為 sink，可以是終端機、檔案，或 Datadog 這類 backend。event 會先進入佇列，等 sink 接上後，再經過採樣與敏感欄位清理，最後送往各個 sink。

evaluation 離線跑，用的是它自己的 task 集（第 23 章）。那組 task 就是拿本章記下來的東西做出來的。

- `emit` 永不阻塞、永不拋例外，所以一次 logging 故障無法卡住或弄垮 loop（第 1 章）。
- event 會先在佇列裡緩衝，等某個 sink 接上再一次送出，所以 loop 在 telemetry 就緒之前就能 log。
- 採樣依速率丟棄 event；scrub 只保留白名單欄位，所以程式碼與路徑永不外洩。
- 成本按模型累加成一個 USD 總額，即時顯示並在退出時顯示。

### 本章新增：fire-and-forget 事件記錄

`telemetry.py` 發出 event。event 先排在佇列裡，等某個 sink 接上，再採樣、scrub，送給每一個 sink。`emit` 永不拋例外：

```python
def emit(self, name, **meta):                          # src/telemetry.py
    if not self.sinks:
        self._queue.append((name, meta))               # buffer until a sink is ready
        return
    self._deliver(name, meta)

def _deliver(self, name, meta):
    if not self.sample(name):                          # dropped by sampling rate
        return
    clean = scrub(meta)                                # allowlist before any backend sees it
    for sink in self.sinks:
        try:
            sink(name, clean)
        except Exception:                              # one bad sink never breaks the loop
            pass
```

- 在任何 sink 接上之前，event 會在 `_queue` 裡緩衝；`attach` 透過同一條 `_deliver` 路徑把它們全部送出去，所以排隊的 event 同樣會被採樣與 scrub。
- `scrub` 只保留 `SAFE_FIELDS`，所以一個未知安全的值（程式碼、檔案路徑、prompt）永遠不會抵達 backend。
- 一個拋例外的 sink 會被吞掉，所以一個壞掉的 backend 無法卡住或弄垮 loop。

### 本章新增：每個模型的成本與離線 eval

成本按模型累加成一個滾動的 USD 總額：

```python
def add(self, model, input_tokens, output_tokens):    # src/telemetry.py
    i, o = self.by_model.get(model, (0, 0))
    self.by_model[model] = (i + input_tokens, o + output_tokens)
    pi, po = PRICES.get(model, (0.0, 0.0))             # modelCost.ts pricing tiers
    self.cost_usd += input_tokens * pi + output_tokens * po
    return self.cost_usd
```

- `add` 查出每 token 的定價，並把花費滾進 `cost_usd`，也就是即時與退出時顯示的那個數字。
- 這個總額算的是整個 session。它說不出錢是花在哪個任務上。

這裡的 `run_eval` 是最小規模的 eval。它把一組固定的 task 集重播到候選 build 上，數過了幾題，回傳一個比率。
第 23 章在同一個入口下面補上環境、模擬使用者和重複執行，也講清楚為什麼比率小幅下滑通常只是雜訊。

### 如何接進現有架構

demo 把 telemetry 掛在 model wrapper 上。loop 不變：

```python
def model(messages, registry, system):
    r = client.messages.create(...)
    cost.add(MODEL, r.usage.input_tokens, r.usage.output_tokens)   # cost rollup
    tel.emit("model_call", model=MODEL, tokens=..., cost_usd=...)  # scrubbed event
    return r
run_turn([...goal...], lambda m, r, s: model(m, r, SYSTEM), reg, Session(mode=DEFAULT))   # the one agent call
```

- telemetry 從外部觀察：wrapper 發出一個 event 並追蹤成本，所以 `run_turn` 與 dispatch 與第 13 章逐位元組相同。
- sink 印出每個 event；session 成本在最後印出；接著一個離線 `run_eval` 為一組固定的 task 集評分。
- 上游的一切都不變。observability 是一個旁觀者，不是 loop 裡的一個新步驟。

### 延伸閱讀

以下設計 `src/` 都沒有實作，出自 ai-agent-book 和兩套 tracing 標準，也未經下面表格的系統證實。

**用 span，不是扁平事件：**一個 span 是一趟 run 裡的一件工作：一次模型呼叫、一次工具呼叫、一次檢索。一條 trace 就是整趟 run。
每個 span 會記下這些：

- 什麼時候開始、跑了多久
- 成功還是失敗
- 它的 parent 是哪一個 span
- 描述這件工作的 attribute，內容可以自己填

真正關鍵的是 parent 這一項。有了它，一趟 run 的 span 就串成一棵樹，從樹頂往下讀，你看得到哪一步失敗、哪一步慢、每一根分支各花了多少錢。

扁平事件做不到這件事。一個 event 只說得出「有這麼一次呼叫」，說不出這次呼叫屬於哪一步。
一個使用者請求可能變成很多次模型呼叫、工具呼叫和檢索，有些巢狀在別人裡面，有些同時在跑。
事後只靠時間戳把它們理清楚，其實是在猜。

span 長什麼樣子，由兩套標準講定，你不用去猜 backend 想吃什麼：

- OpenTelemetry 定義 span 本身：trace id、parent id、時間、狀態、attribute。
- OpenInference 在上面補 LLM 這一塊的名稱：prompt、completion、model、token 數、工具呼叫。

照這套名稱把點埋一次就好，之後換 backend 只是改設定，不用重寫。

匯出跟 `emit` 守同一條規矩：留在熱路徑之外。span 先進佇列，由背景 worker 分批送出，這樣 collector 再慢，run 也一點都不受影響。
本章的 `emit` 就是它的扁平版；在同一批 event 上補一個 trace id 和一個 parent id，樹就出來了。

**非線性的成本與每任務上限：**成本算的是模型讀進去多少 token，而每一輪都要把整段對話重送一次。
所以第二輪回傳的工具結果，第三、第四、第五輪還要再付一次錢。
context 裡多出來的任何東西，後面每一輪都得再付一遍，總額爬升的速度比輪數快。光看步數，你算不出這筆帳。

有兩個 harness 功能各砍掉帳單的一塊，但兩邊省下來的不能相加：

- prompt caching（第 10 章）把沒變過的那段前綴打折。
- compaction（第 8 章）把比較舊的對話從 context 裡拿掉。

它們會重疊：compaction 拿掉的，正好是 caching 本來就會打折的那些 token。

一個 session 總額把這些全蓋住了，因為它說不出錢是花在哪個任務上。
所以成本要算到任務層級，而且每個任務都給一個上限。上限攔下一趟 run，跟步數上限攔下停不下來的 loop 是同一招（第 1 章）。
這件事只有書講，書裡也沒有引外部來源，所以這套成本模型就當作者自己的現場經驗看。

**trace 回流成 eval 集：**兩條 pipeline 只在一個方向上交會：正式環境的一條 trace 變成一個 eval task。中間有三個步驟。

- **挑：**留下值得學的那幾趟：報錯的、使用者重試或出手糾正的、花費遠高於其他的。一趟順順利利的 run 給不了新東西。
- **脫敏：**擋住程式碼和路徑不進 backend 的那份白名單，同樣擋住它們進 task 檔。
- **重建：**一條 trace 裡有起始狀態和每一次工具呼叫，所以它給得出這個 task 的起點，也給得出這趟 run 本來該走到的結果。

這件事持續做，eval 集才跟得上使用者真正在做的事。落到那裡的東西，由第 23 章來評分。

---

## 不同系統怎麼做

每個 agent 如何發出 telemetry、追蹤花費，以及怎麼餵養 eval 集。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | 低成本又安全地換來豐富的正式環境可見度。 | crash 掉的 run 也留得下檔案。 | 不用另外埋點：模型看得到的，log 裡就有。 |
| **限制** | 只說發生了什麼，答案好不好看不出來。 | 正式環境 telemetry 幾乎沒有。 | 沒附任何脫敏規則。送出去可能會漏，也可能重複。 |
| **設計原因** | 正式環境得盯住當機和成本，又不能碰 loop。 | 品質靠離線 benchmark 評分，完整紀錄最重要。 | session log 本來就是紀錄，直接把它送出去就好。 |
| **做法：telemetry** | event 先排隊，等 sink 接上再採樣、scrub。 | 每趟 run 一個軌跡檔，每一步都存。 | 每一則 session 事件都經過脫敏那一關再鏡射出去。 |
| **做法：cost tracking** | 每模型 token 按定價滾成一個 session 總額。 | 逐次計價，彙總成 run 與全域總額。 | 重放整份 log 算出 token 數，從不換算成錢。 |
| **做法：eval feed** | 原始碼中沒有；trace 脫敏後變成 regression 案例。 | 存下來的軌跡餵給 benchmark runner。 | 錄下來的 run 不用金鑰就能重放，當成固定樣本。 |

---

## 常見問題

- **telemetry 落在熱路徑上：**一個會阻塞或拋例外的 logging 呼叫會卡住 loop（第 1 章）。一個要等網路回應的 span exporter 也一樣。
  緩解：呼叫完不等結果，搭配 pre-sink 佇列、每 sink killswitch，以及背景 worker 分批匯出。
- **敏感資料洩漏到 log：**程式碼、檔案路徑或 prompt 跑進一個一般存取的 backend，或跑進由 trace 生出來的 task 檔。
  緩解：白名單可記錄欄位，送出或存檔之前 scrub 掉其餘。
- **扁平事件流沒有 parent 連結：**沒人說得出哪次模型呼叫屬於哪一步，一趟失敗的 run 只能靠時間戳慢慢拼。
  緩解：每個 event 都帶 trace id 和 parent span id，用 backend 本來就認得的命名慣例。
- **成本漂移沒被察覺：**一次模型替換或失控 loop 會讓花費倍增，而一個 session 總額會蓋掉那個真正在燒錢的任務。
  緩解：每模型與每任務的總額都即時和退出時顯示，加上每任務的上限，還有 loop 的步數上限（第 1 章）。
- **eval 集跟正式環境脫節：**離線 task 漏掉了真實用法，於是套件通過而使用者失敗（第 23 章）。
  緩解：持續把失敗和昂貴的 run 脫敏之後篩進 task 集。

---

## 動手跑跑看

[`src/`](src/) 承接第 19 章並加上：

- [`telemetry.py`](src/telemetry.py)：event logger（`Telemetry.emit`、排隊與送出、`sample`、`scrub`）、每模型的 `CostTracker`，以及離線的 `run_eval`。
- [`test.py`](src/test.py)：先排隊再送出、採樣、scrub 加上真實工具 dispatch 上的 sink 隔離、每模型成本，以及一個抓到退步 build 的 eval。
- [`demo.py`](src/demo.py)：一輪 agent 由掛在 model wrapper 上的 telemetry 觀察、一個即時 session 成本，接著一個離線 eval。

loop 與 dispatch 都不變。telemetry 從外部觀察，而被它餵養的 eval 在熱路徑之外跑（第 23 章）。

```bash
python sections/20-observability/src/test.py         # offline checks, no key
uv run python sections/20-observability/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code analytics](https://github.com/yasasbanukaofficial/claude-code)：
  `services/analytics/index.ts`（queue + `logEvent`）、`sink.ts`、`datadog.ts`、`firstPartyEventLogger.ts`、`sinkKillswitch.ts`、`shouldSampleEvent`。
- [Claude Code cost and diagnostics](https://github.com/yasasbanukaofficial/claude-code)：
  `cost-tracker.ts`、`utils/modelCost.ts`、`costHook.ts`（`formatTotalCost`）、`diagnosticTracking.ts`、`upstreamproxy/relay.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/telemetry.md`、`docs/subsystems/token-meter.md`、`packages/llm/llm-replay/README.md`、`docs/subsystems/invariants.md`。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter6.md`，以中文原書為準。
  span 樹、非線性的 agent 成本與每任務上限，還有正式環境 trace 回流成 eval 集。
  書裡的成本分析沒有引用外部來源，屬於單一來源。
- [OpenTelemetry tracing](https://opentelemetry.io/docs/specs/otel/trace/api/)：span 本身、parent 連結、時間、狀態與 attribute。
- [OpenInference](https://github.com/Arize-ai/openinference)：在 span 上替 LLM 與工具 attribute 命名的語意慣例。
- evaluation 不在 Claude Code 這份原始碼裡，在這個 repo 由第 23 章負責。保留的 task 集與 LLM-as-judge 仍以重建與一般做法描述。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py`、`models/__init__.py`、`run/benchmarks/swebench.py`、`run/utilities/inspector.py`。
- 章節定位：[learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code)。
