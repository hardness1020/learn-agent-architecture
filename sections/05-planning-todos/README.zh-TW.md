# 5 · Planning & todos

[English](README.md) · **繁體中文** · [简体中文](README.zh-CN.md)

> 面對多步驟任務，先把計畫寫下來，再開始動手。

大型任務需要一份明確、可追蹤的計畫。如果計畫只藏在 prompt 裡，經過多輪工具呼叫後，模型很容易忘記原本的進度。

規劃解決了兩個各自獨立的問題：

1. agent 工作時需要一份隨時可更新的檢查清單。
2. agent 還沒理解任務前，不應該直接修改檔案。

本章會同時加入 todo 工具和 plan mode。todo 工具保存檢查清單；plan mode 則把 agent 限制在唯讀探索，直到計畫通過核准。

短任務沒有這一層通常也能完成，但任務一長，agent 就容易漏掉步驟，或在資訊還不足時太早動手。

---

## 核心機制

![機制圖](assets/05-planning-and-todos.png)

這裡加入兩個一般的模型工具，核心 loop 完全不需要修改。

- **Todo list：**模型會覆寫一份結構化的檢查清單。這個工具不會碰檔案或 shell，只負責保存目前 session 的計畫狀態。
- **Plan mode：**session 進入唯讀模式後，模型可以探索並撰寫計畫，但必須呼叫 `ExitPlanMode` 並通過 permission gate，才能開始修改。

### 本章新增：todo 與 plan mode 工具

```python
@dataclass
class Session:                                   # src/loop.py: mutable, outlives a turn
    mode: str = DEFAULT
    todos: list = field(default_factory=list)

def todo_tool(session):                          # src/planning.py
    def write(a): session.todos = list(a["todos"])    # model overwrites its checklist
    return Tool("TodoWrite", write, is_read_only=True)    # no side effect, never gated

def exit_plan_mode_tool(session):                # src/planning.py
    def exit_plan(_): session.mode = ACCEPT_EDITS     # approval flips the live mode
    return Tool("ExitPlanMode", exit_plan)
```

- `Session` 現在存放 `mode` 與 `todos`。
- `TodoWrite` 只更動 `session.todos`，所以從外部看它是唯讀的。
- `ExitPlanMode` 在核准後改變 `session.mode`。
- 下一次工具呼叫會透過同一個 permission gate 讀到新的 mode。

### 如何接進現有架構

第 3 章的 permission 邏輯已經認得 `PLAN`：

```python
if mode == PLAN:                              # exploring, not acting yet
    if tool.is_read_only:           return "allow"
    if tool.name == "ExitPlanMode": return "ask"     # the approval handshake
    return "deny"                             # no edits until the plan is approved
```

第 5 章加入的是工具和 session 狀態。它並沒有加入新的 loop 或新的 permission 路徑。

一個 todo 項目是 `{ content, status, activeForm }`。

status 是 `pending`、`in_progress` 或 `completed`。模型每次都會寫入整份清單，而 harness 負責把目前狀態渲染出來。

---

## 不同系統怎麼做

各個 agent 如何追蹤計畫並管制執行。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **優點** | 簡單又便宜。memory 中的 todo list 沒有相依，也沒有鎖。 | 計畫與 todo 狀態撐得過重啟、fork 與 compaction。 |
| **限制** | 只是 session 狀態。要跨 turn 存活的工作得交給 task graph（見第 12 章）。 | plan mode 自己擋不住任何東西，要 sandbox 或 approval policy 出手才擋得下編輯。 |
| **設計原因** | 計畫只留在 prompt 裡會走丟，而且計畫核准前不該動檔案。 | session log 才是唯一的事實來源，所以計畫狀態也只是一則事件。 |
| **做法：plan artifact** | 一份 todo list 加一個 plan 檔。`TodoWrite` 覆寫清單，從不被管制。 | `todo_write` 把整份清單當成一則事件附加上去，重放事件就能還原清單。 |
| **做法：plan mode** | 有。進入時把 permission mode 切成 plan，session 維持唯讀。 | 一個寫進 log 的旗標，加上 prompt 裡的一段指引文字，不動 permission。 |
| **做法：execution gate** | `ExitPlanMode` 請求核准。不在 plan mode 時，這個呼叫會被拒絕。 | 規劃期間完全不管制。計畫被打回來時，以 tool feedback 回傳。 |

---

## 常見問題

- **清單過時：**模型不再更新 todos。要提醒它讓一個項目保持 `in_progress`，並在工作完成時關閉項目。
- **對小工作過度規劃：**為一個單步驟的任務列 todo list 會增加雜訊。瑣碎的任務就略過它。
- **Plan mode 無法離開：**有些介面無法顯示核准對話框。在那些介面上要把進入與離開一起停用。
- **沒進入就離開：**模型可能在不對的 context 下呼叫 `ExitPlanMode`。要驗證目前的 mode 是 `plan`。
- **計畫隨 context 消失：**一份扁平的 todo list 是 session 狀態。當工作必須跨越一個 turn 或程序而存活時，要改用 task 系統。

---

## 動手跑跑看

[`src/`](src/) 承接 04 並加上：

- [`planning.py`](src/planning.py)：`TodoWrite` 與 `ExitPlanMode`。
- [`loop.py`](src/loop.py)：持有一個 `Session`，讓 mode 可以在執行中途改變。
- [`test.py`](src/test.py)：檢查 todo 寫入、plan-mode 拒絕、核准，以及編輯執行。

```bash
python sections/05-planning-todos/src/test.py         # offline checks, no key
uv run python sections/05-planning-todos/src/demo.py  # live demo, needs a key
```

---

## 參考資料

- [Claude Code 原始碼](https://github.com/yasasbanukaofficial/claude-code)：
  `tools/TodoWriteTool/TodoWriteTool.ts`、`tools/EnterPlanModeTool/EnterPlanModeTool.ts`、`tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`。
- [Claude Code planning helpers](https://github.com/yasasbanukaofficial/claude-code)：`utils/plans.ts`、`utils/todo/types.ts`、`types/permissions.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/todo/tool-todo/src/index.ts`、`packages/plan/plan-mode/src/index.ts`、`docs/subsystems/plan.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s05_todo_write](https://github.com/shareAI-lab/learn-claude-code)：section framing。
