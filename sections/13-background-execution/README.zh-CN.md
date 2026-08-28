# 13 · Background execution

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 把耗时工作移到后台执行，主 loop 可以先继续处理其他事。

有些操作需要很长时间，例如安装依赖、建置、执行完整测试、整理 memory，或启动一个拥有自己 loop 的 subagent。

基本的 agent loop 会等工具调用完成，才进行下一次 model call。

这对快速读取没有问题，但有些工作跑得很久，让整个 loop 原地等待就很浪费。这类工作其实可以自己在后台跑，agent 同时继续处理其他事项。

background execution 必须：

1. 判断哪些操作适合用非阻塞方式执行。
2. 启动工作后立刻返回 handle。
3. 追踪 `running`、`completed`、`failed` 和 `killed` 等状态。
4. 工作完成后，再把通知送回 loop。

少了这一层，一个耗时指令就可能卡住整个 agent。

---

## 核心机制

![机制图](assets/13-background-execution.png)

后台执行由三个部分组成：

1. starter：把工作移出 loop，并返回 handle。
2. runtime：持续追踪 task 状态。
3. queue：等工作完成后，在后续 turn 注入 notification。

loop 不会停下来等这件工作跑完。

- 后台执行是一个执行选项，而不是一种特殊的工具类型。
- 被后台化的调用会立刻返回一个正常的 `tool_result`。
- 真正的结果稍后才会用另一则 notification 送进来。
- 一整个 subagent 也可以在后台执行。

### 本章添加：在 loop 外启动工作，把 notification 收进对话

`start` 在一个 worker thread 上跑工作，并返回一个 task id：

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

`drain_into` 把已完成的 notification 并入下一个 user turn：

```python
def drain_into(messages, runtime):                     # src/background.py
    notes = runtime.drain() if runtime else []
    if notes and messages and isinstance(messages[-1].get("content"), str):
        messages[-1]["content"] = "\n".join(notes) + "\n\n" + messages[-1]["content"]
```

`backgroundable` 包装任何工具，并在它的 schema 加上 `run_in_background`：

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

这层包装也决定了 model 会拿到什么。丢到后台的调用只是把工作启动起来：它返回一个 task id，结果稍后才用自己的那则事件送回来。
跑很久的工具，名字和描述就照这样写（`initiate_export`，不要写成 `export`）。model 才会把当下那则 `tool_result` 读成收据，而不是答案。

### 如何集成到现有架构

loop 在一个 turn 开始时，把 queue 里累积的完成 notification 收进对话：

```python
background.drain_into(messages, runtime)               # src/loop.py
```

「一个工具调用对一个工具结果」的规则依然成立。一则迟来的完成 notification，不是给旧 `tool_use_id` 的延迟 `tool_result`。它是一则全新的 notification 消息。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book，也未经下面表格的系统证实。

**打断与安全点：**有些消息不能等目前这个工具调用跑完。
用户的修正、一个取消、一则警报，都可能在调用跑到一半时进来。一种做法是把所有进来的消息都变成同一条 stream 上的 event。
loop 只在安全点（safe point）去读这条 stream，也就是一则工具结果刚跑完、下一次 model 调用还没发出的那个空档。
调用跑到一半硬塞会弄坏对话记录，所以 event 得等那个空档。

event 有多急，决定它要等哪一个空档：

- **Queue：**等下一个空档。完成通知和不急的消息都走这条。
- **Cancel：**直接中止进行中的调用，当场空出一个空档。适合那种再跑下去也白跑的修正。
- **Parallel：**丢到旁边的 loop 去跑，主 loop 不动。

分类这件事本身不贵。用一个小 model 就能把 event 分成这三类，每则 event 只多花一次调用。

**打断占位：**取消完还要多做一步，对话记录才会是合法的。
被中止的那个调用留下一个 `tool_use` block，却没有对应的 `tool_result`，而下一次 model 调用需要这一对是完整的。
ai-agent-book 的做法是当场补：对同一个 id 补一则占位用的 `tool_result`，内容写这个调用被中断了。
这跟上面那条不重用 id 的规则不冲突。占位的那则当下把这一对收干净，真的结果照样稍后用自己的 notification 送进来。
占位这套是书作者自己提的设计，目前没有第二个出处。

---

## 不同系统怎么做

各个 agent 如何把工作移出 loop，又如何回报完成。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **优点** | 吞吐量提升，也不再有闲置的等待。单纯的等待不会卡住任何东西。 | 同一个登记处管 shell、终端机和 child agent，收结果和喊停都走同一条路。 |
| **限制** | 结果可能较晚抵达，顺序也可能颠倒。runtime 要顾状态和清理。 | 叫醒闲着的 agent 会花掉模型轮次，所以得给它一个额度。 |
| **设计原因** | 一个跑很久的指令不该冻结整个 agent。 | 工作跑完要让模型知道，而不是叫模型自己一直去问。 |
| **做法：off-loop primitive** | 后台 shell task 和后台 agent task，subprocess 继续跑，输出被转导。 | 任何工具都能带一个「丢到后台跑」的标志，返回一个 job id。 |
| **做法：notification** | 一则 `<task_notification>` 消息，完成消息走同一个共享 queue。 | 每个 job 一则通知。谁先结束谁算数，重复的会被压下来。 |
| **做法：re-entry** | notification 在 turn 之间收进对话，分 `now`、`next`、`later` 三种优先级。 | agent 忙的话下一步就收到；闲着就叫醒它，次数有上限。 |

---

## 常见问题

- **互动式提示卡住（Interactive prompt stalls）：**某个后台指令在等输入。检测像提示的输出，并通知 model 去 kill 它，或以非互动方式重跑。
- **完成消息遗失（Lost completion）：**某个完成的 task 从没抵达 loop。让完成消息走同一个共享 queue，并把 task 标记为已通知。
- **配对错误的 notification（Mispaired notification）：**重用旧的 `tool_use_id` 会弄坏 transcript。改用独立的 notification 文字。
- **被 kill 之后的副作用（Side effect after a kill）：**timeout 或取消都不会告诉你那个调用到底做成了没。盲目重试可能扣两次款。先查状态再写入，或带上 idempotency key。
- **批次 event 稀释注意力（Batched events dilute attention）：**一次 drain 可能把好几则 notification 并进同一个 turn，model 就只响应最后一则。帮每则 event 编号，再加一行摘要。
- **并行太多（Too much concurrency）：**太多后台 task 会耗尽资源。加上 kill 路径和上限。
- **离场时的 process 泄漏（Process leak on exit）：**后台工作可能活得比 session 还久。注册清理机制。

---

## 动手跑跑看

[`src/`](src/) 把 12 带了过来，并加上：

- [`background.py`](src/background.py)：一个 runtime、notification queue、`drain_into`，以及 `backgroundable`。
- [`loop.py`](src/loop.py)：在调用 model 前，把待处理的 notification 收进对话。
- [`test.py`](src/test.py)：检查 start、failure、drain，以及后台 subagent。
- [`demo.py`](src/demo.py)：在后台启动一个 subagent，稍后再读取它的结果。

```bash
python sections/13-background-execution/src/test.py         # offline checks, no key
uv run python sections/13-background-execution/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code task sources](https://github.com/yasasbanukaofficial/claude-code)：`tasks/LocalShellTask/`、`tasks/DreamTask/`。
- [Claude Code tool and queue sources](https://github.com/yasasbanukaofficial/claude-code)：
  `tools/BashTool/BashTool.tsx`、`tools/SleepTool/prompt.ts`、`utils/task/framework.ts`、`utils/messageQueueManager.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/jobs/jobs/src/index.ts`、`packages/jobs/jobs-local/src/index.ts`、`packages/jobs/tool-jobs/README.md`、
  `docs/subsystems/jobs.md`、`docs/tool-catalog.md`。
- [learn-claude-code · s13_background_tasks](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter4.md`，以中文原版为准。
  幂等性与取消语义、启动与完成分开命名、在安全点做 event 分类、打断占位、批次 event 的注意力稀释。
