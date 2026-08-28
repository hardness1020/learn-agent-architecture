# 6 · Subagents

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 把一個明確的子問題交給獨立 loop，只拿回需要的結論。

主 agent 可以把工作交給 subagent。負責委派的一方叫 parent，接下任務的一方叫 child。

對 parent 來說，委派看起來只是一個 tool call，但工具內部其實會啟動完整的 agent loop。parent 傳入 prompt，child 使用全新的 `messages[]` 執行任務，完成後只回傳最終答案。

這樣做能把旁支調查留在 child 的 context 裡。parent 不需要看到 child 讀過的每個檔案和每段指令輸出，通常只需要最後的結論。

沒有 subagent，所有調查過程都會堆在主 transcript 中。任務愈長，context 就愈雜亂、成本愈高，模型也更難抓住重點。

---

## 核心機制

![機制圖](assets/06-subagents.png)

`Agent` tool 會啟動一個 child agent。child 擁有自己的 session 和 message 清單，但執行的仍是和 parent 相同的 loop。

回傳給 parent 的只有 child 最後輸出的文字，child 的 transcript 不會一併帶回。要注意的是，檔案寫入與 shell 指令造成的副作用仍會留在工作目錄中。

### 本章新增：Agent tool

```python
def agent_tool(model, child_registry, parent_session):     # src/subagents.py
    def spawn(a):
        child = Session(mode=parent_session.mode,          # fresh context, inherited authority
                        allow_rules=set(parent_session.allow_rules))
        messages = [{"role": "user", "content": a["description"]}]   # the child's own conversation
        return run_turn(messages, model, child_registry, child)      # the loop, run again
    return Tool("Agent", spawn, is_read_only=True)
```

- `agent_tool` 回傳一個一般的 tool。
- 它的 handler 用一個新的 `Session` 呼叫 `run_turn()`。
- child 的 `messages[]` 一開始只有 child 的 prompt。
- child 回傳 `run_turn()` 所回傳的文字。

### 如何接進現有架構

loop 不會改變。subagent 只是另一個呼叫 loop 的 tool handler。

有三個特性很重要：

- **全新 context：**child 不會繼承 parent 的 transcript。parent 也不會繼承 child 的軌跡。
- **繼承的權限：**child 會複製 parent 的 permission mode 和 allow rules。 context 隔離不等於權限隔離。
- **遞迴上限：**這個 demo 從 child registry 中省略了 `Agent`，所以 child 無法再生出另一個 child。

---

## 不同系統怎麼做

各 agent 如何隔離一個子問題，並回傳結果。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **優點** | child 的 context 讓 parent 保持聚焦，主 transcript 也乾淨。 | 同一個擴充點涵蓋行程內的 child、外部 runtime，以及產品 CLI。 |
| **限制** | parent 不知道 child 是怎麼得出答案的，摘要太薄就得再問一次。 | 一個工具能解決的事，這裡有六種後端加一套續跑管理。 |
| **設計原因** | parent 只需要結論，不需要 child 讀過的每個檔案。 | 委派只是傳輸方式的選擇，所以每種後端都掛在一個名字下。 |
| **做法：spawn primitive** | `Agent` tool。用 subagent type 選一個內建 persona。 | 每個註冊的後端各有一個工具：全新 child、fork、外部 runtime 或 CLI。 |
| **做法：context isolation** | child 的 messages 是全新的。fork 出來的 child 不能再 fork。 | 全新 child 從空的開始。fork 只複製 parent 已經跑完的 turn。 |
| **做法：result return** | child 最後一則訊息的文字回傳給 parent，transcript 丟棄。 | 最後一則 assistant 訊息，外加可選的結構化輸出，會照 schema 檢查。 |
| **做法：resume** | 多數 agent 可以續跑，parent 再發一則訊息就好。 | durable 的 child 會把後續訊息排進佇列，重啟後也能從 log 重新載回。 |

---

## 常見問題

- **摘要遺漏資訊：**child 可能壓縮過頭。要求它把重要發現寫到硬碟上。
- **失控遞迴：**child 生 child 可能無上限地成長。從 child registry 省略 `Agent` tool，或強制設一個深度上限。
- **child 停不下來：**child 和 parent 有一樣的停止風險。給每個 child 自己的 turn 或 token 上限。
- **誤以為有權限隔離：**child 仍然需要正常的 permission gate。不要因為 context 是分開的就跳過它。
- **孤兒非同步 child：**一個背景 child 可能在 parent 已經往前走之後才結束。用一筆 task 記錄來追蹤它。

---

## 動手跑跑看

[`src/`](src/) 沿用 05 並加上：

- [`subagents.py`](src/subagents.py)：`Agent` tool。
- [`loop.py`](src/loop.py)：與第 5 章相同，未變動。
- [`demo.py`](src/demo.py)：parent 把一個計數任務委派給 child。
- [`test.py`](src/test.py)：檢查全新 context、繼承的權限，以及遞迴防護。

```bash
python sections/06-subagents/src/test.py         # offline checks, no key
uv run python sections/06-subagents/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 原始碼](https://github.com/yasasbanukaofficial/claude-code)：
  `tools/AgentTool/AgentTool.tsx`、`runAgent.ts`、`resumeAgent.ts`、`forkSubagent.ts`、`builtInAgents.ts`、`tasks/LocalAgentTask/`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/subagent/subagent/src/index.ts`、`src/continuation.ts`、`packages/subagent/subagent-fork-in-process/README.md`、
  `packages/subagent/subagent-acp/README.md`、`docs/subsystems/subagent.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s06_subagent](https://github.com/shareAI-lab/learn-claude-code)：章節框架。
