# 5 · Planning & todos

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md)

> 面对多步骤任务，先把计划写下来，再开始动手。

大型任务需要一份明确、可追踪的计划。如果计划只藏在 prompt 里，经过多轮工具调用后，模型很容易忘记原本的进度。

规划解决了两个各自独立的问题：

1. agent 工作时需要一份随时可更新的检查列表。
2. agent 还没理解任务前，不应该直接修改文件。

本章会同时加入 todo 工具和 plan mode。todo 工具保存检查列表；plan mode 则把 agent 限制在只读探索，直到计划通过批准。

短任务没有这一层通常也能完成，但任务一长，agent 就容易漏掉步骤，或在信息还不足时太早动手。

---

## 核心机制

![机制图](assets/05-planning-and-todos.png)

这里有两个工具。两个都是一般的模型工具，核心 loop 完全不需要修改。

**Todo list。**模型会覆盖一份结构化的检查列表。这个工具不会碰文件或 shell，只负责保存目前 session 的计划状态。

**Plan mode。**session 进入只读模式。模型可以探索、写下计划，然后调用 `ExitPlanMode`。这个离开动作由 permission 层把关。

### 本章添加：todo 与 plan mode 工具

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

- `Session` 现在存放 `mode` 与 `todos`。
- `TodoWrite` 只更动 `session.todos`，所以从外部看它是只读的。
- `ExitPlanMode` 在批准后改变 `session.mode`。
- 下一次工具调用会通过同一个 permission gate 读到新的 mode。

### 如何集成到现有架构

第 3 章的 permission 逻辑已经认得 `PLAN`：

```python
if mode == PLAN:                              # exploring, not acting yet
    if tool.is_read_only:           return "allow"
    if tool.name == "ExitPlanMode": return "ask"     # the approval handshake
    return "deny"                             # no edits until the plan is approved
```

第 5 章加入的是工具和 session 状态。它并没有加入新的 loop 或新的 permission 路径。

一个 todo 项目是 `{ content, status, activeForm }`。

status 是 `pending`、`in_progress` 或 `completed`。模型每次都会写入整份列表，而 harness 负责把目前状态渲染出来。

---

## 不同系统怎么做

各个 agent 如何追踪计划并控制执行。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **优点** | 简单又便宜。放在内存里的 todo list 没有依赖，也没有锁。 | 计划与 todo 状态能经受重启、fork 与 compaction。 |
| **限制** | 只是 session 状态。要跨 turn 存活的工作得交给 task graph（见第 12 章）。 | plan mode 自己挡不住任何东西，要 sandbox 或 approval policy 出手才挡得下编辑。 |
| **设计原因** | 计划只留在 prompt 里会走丢，而且计划批准前不该动文件。 | session log 才是唯一的事实来源，所以计划状态也只是一则事件。 |
| **做法：plan artifact** | 一份 todo list 加一个 plan 档。`TodoWrite` 覆盖列表，从不被控制。 | `todo_write` 把整份列表当成一则事件附加上去，重放事件就能还原列表。 |
| **做法：plan mode** | 有。进入时把 permission mode 切成 plan，session 维持只读。 | 一个写进 log 的标志，加上 prompt 里的一段指引文字，不动 permission。 |
| **做法：execution gate** | `ExitPlanMode` 请求批准。不在 plan mode 时，这个调用会被拒绝。 | 规划期间完全不控制。计划被打回来时，以 tool feedback 返回。 |

---

## 常见问题

- **列表过时：**模型不再更新 todos。要提醒它让一个项目保持 `in_progress`，并在工作完成时关闭项目。
- **对小工作过度规划：**为一个单步骤的任务列 todo list 会增加噪声。琐碎的任务就略过它。
- **Plan mode 无法离开：**有些接口无法显示批准对话框。在那些接口上要把进入与离开一起停用。
- **没进入就离开：**模型可能在不合适的 context 下调用 `ExitPlanMode`。要先验证当前 mode 是否为 `plan`。
- **计划随 context 消失：**一份扁平的 todo list 是 session 状态。当工作必须跨越一个 turn 或进程而存活时，要改用 task 系统。

---

## 动手跑跑看

[`src/`](src/) 承接 04 并加上：

- [`planning.py`](src/planning.py)：`TodoWrite` 与 `ExitPlanMode`。
- [`loop.py`](src/loop.py)：持有一个 `Session`，让 mode 可以在执行中途改变。
- [`test.py`](src/test.py)：检查 todo 写入、plan-mode 拒绝、批准，以及编辑执行。

```bash
python sections/05-planning-todos/src/test.py         # offline checks, no key
uv run python sections/05-planning-todos/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：
  `tools/TodoWriteTool/TodoWriteTool.ts`、`tools/EnterPlanModeTool/EnterPlanModeTool.ts`、`tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`。
- [Claude Code planning helpers](https://github.com/yasasbanukaofficial/claude-code)：`utils/plans.ts`、`utils/todo/types.ts`、`types/permissions.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/todo/tool-todo/src/index.ts`、`packages/plan/plan-mode/src/index.ts`、`docs/subsystems/plan.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s05_todo_write](https://github.com/shareAI-lab/learn-claude-code)：section framing。
