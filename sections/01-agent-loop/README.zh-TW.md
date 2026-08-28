# 1 · Agent Loop

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> loop 會反覆呼叫模型、執行工具，再把結果送回去，直到這一輪完成。

一般的模型呼叫就是一問一答：送出 messages，收到一次回應，流程便結束。

agent 不只要取得回應，還要執行模型要求的工具，把結果加回對話，再呼叫一次模型。因此，同一份 `messages[]` 會在整個輪次中持續累積內容。

這個 loop 必須：

1. 在多次模型呼叫之間保留對話狀態。
2. 判斷模型要使用工具，還是已經給出最終答案。
3. 執行指定的工具，並把結果加入對話。
4. 重複以上流程，直到模型結束這一輪。

沒有這個 loop，模型只能判斷該怎麼做，卻不能真的動手。loop 若設計錯誤，可能太早停止，也可能永遠停不下來。

---

## 核心機制

![機制圖](assets/01-agent-loop.png)

這裡要分清楚兩層 loop，它們共用同一份 `messages[]`。

可以把它想成一個聊天視窗。你問「台北現在天氣如何？要不要帶傘？」，模型可能先查目前天氣，再查降雨機率，最後才整理成答案。
**所以同一個輪次裡，模型往往被呼叫好幾次，中間穿插各種工具呼叫。**
從發問到收到完整答案的整段流程，就是**內層 loop**，也就是一個使用者輪次（turn）。它會呼叫模型、檢查 `stop_reason`、視需要執行工具並加入結果，直到模型給出最終答案。

接著你在同一個視窗再問「那明天呢？」，這就是新的一輪。
負責把多個輪次串成完整對話的，則是**外層 loop**。每一輪都會加到同一份 `messages[]`，所以當你接著問「那明天呢？」，模型仍然知道前面談的是台北天氣。

內層 loop 就是拿著呼叫端手上那份 `messages[]`，把一輪跑完：

```python
def run_turn(messages, model, max_steps=10):        # src/loop.py · one turn over the shared messages[]
    for _ in range(max_steps):                       # the inner loop, with a backstop
        response = model(messages)                   # one Anthropic Messages call
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":       # model produced its answer for this turn
            return final_text(response)

        results = []                                 # tool_use: run each, feed back
        for block in response.content:
            if block.type == "tool_use":
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": run_tool(block.name, block.input)})
        messages.append({"role": "user", "content": results})

    raise RuntimeError("hit max_steps without end_turn")
```

- [`src/loop.py`](src/loop.py) 中的 `run_turn()` 就是內層 loop。
- `messages` 是採用 Anthropic Messages 格式的共享狀態。
- `max_steps` 是防止 loop 失控的安全上限。
- `run_tool(name, input)` 解析出工具、執行它，並回傳供 `tool_result` 使用的文字。
- [`src/demo.py`](src/demo.py) 中的 `model()` 是一次 `client.messages.create` 呼叫。loop 不綁定單一供應商。

外層 loop 每一輪附加一則使用者訊息，並保留整個緩衝區：

```python
messages = []                                        # src/demo.py · the conversation, owned by the caller
for user_text in turns:                              # the outer loop: one iteration per user turn
    messages.append({"role": "user", "content": user_text})
    reply = run_turn(messages, model)                # appends in place; turn N sees turns 1..N-1
```

有兩個 `stop_reason` 值驅動這個 loop：

- `tool_use`：執行工具、附加結果，再次呼叫模型。
- `end_turn`：回傳最終答案。demo 只要遇到任何不是 `tool_use` 的值就停止。

`messages[]` 是這個 session 的整段對話記憶。工具結果與 assistant 回覆都會放進去。下一次模型呼叫會在這整份狀態上進行推理。

這個最精簡的 loop 沒有權限關卡。第 3 章會在工具執行前加上權限。

---

## 不同系統怎麼做

各個 agent 如何擁有這個 loop，以及如何決定何時停止。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **優點** | 能串流進度、把關副作用，還能平行執行工具。 | loop 很小，容易閱讀與稽核。 | loop 可以整個換掉，每個階段都能攔截，log 可以重放。 |
| **限制** | loop 包在一個更大的 runtime 裡，不能單獨拿出來用。 | 無法把關副作用、串流進度，或平行執行工具。 | 活動零件最多。得先懂 turn、step、inbox 這套詞彙。 |
| **設計原因** | 核心分支保持不變，功能都加在外圍。 | 小 loop 本身就是目的。偵測任務是否完成的是環境，不是模型。 | loop 就是眾多 plugin 裡的一個。 |
| **做法：loop driver** | 一個 async generator。每個工具透過同一份契約接進 dispatch。 | 一個 while loop。每一步跟模型要一道指令，再執行。 | 一個可換掉的 plugin，跑在一份 durable 事件 log 上。 |
| **做法：stop signal** | `stop_reason: end_turn`。 | 由環境偵測提交標記，附加一則 `role: "exit"` 訊息。 | 沒有待處理項目、檢查點沒有攔截，或某個 tool result 直接結束這一輪。 |
| **做法：parallel tools** | 有。同一次模型輪次中的工具呼叫可以平行執行。 | 沒有，action 依序執行。 | 有。exclusive 呼叫形成 barrier，安全呼叫共用一個有上限的池。 |
| **做法：streaming** | 有。模型 token、工具呼叫與工具結果發生的當下就逐一送出。 | 沒有。 | 有。串流 chunk 以 durable 事件寫進 session log。 |

---

## 常見問題

- **沒有停止條件：**一個 bug 或工具 loop 可能永遠跑下去。用最大步數或 token 上限。
- **loop 中途 context overflow：**`messages[]` 只會成長。第 8 章加上 context 管理。
- **部分工具失敗：**失敗的工具仍必須回傳一個 `tool_result`，模型才能復原。
- **結果遺失：**丟掉 assistant 的工具呼叫或工具結果任何一個，都會破壞 transcript。兩者都要附加。

---

## 動手跑跑看

[`src/`](src/) 從這裡開啟整條鏈：

- [`loop.py`](src/loop.py)：內層 loop 與共享的 `messages[]`。
- [`demo.py`](src/demo.py)：兩輪的即時 demo。第 2 輪仰賴第 1 輪仍留在緩衝區裡。
- [`test.py`](src/test.py)：針對工具 dispatch、最終文字與多輪狀態的離線檢查。

第 2 到 11 章會把這份 `src/` 帶著往前走，持續演進 `loop.py`，並在每一章加上一個檔案。

```bash
python sections/01-agent-loop/src/test.py         # offline checks, no key
uv run python sections/01-agent-loop/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code)：`QueryEngine.ts`、`query/`、`Tool.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py`、`exceptions.py`、`environments/local.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/architecture.md`、`docs/agent-lifecycle.md`、`docs/subsystems/core.md`、`packages/core/agent-loop/src/agent.ts`、`packages/core/agent/src/types.ts`。
- [learn-claude-code · s01 Agent Loop](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
