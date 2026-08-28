# 18 · Autonomy

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 不必等待用户下指令，agent 闲置时也能主动认领 task 并开始工作。

autonomy 指的是：即使没有用户 prompt 触发新一轮，第 1 章的 agent loop 仍能持续找到并执行工作。

最直觉的团队设计，是由 lead 把下一个 task 逐一分配给各个 worker。

但这种集中派工方式不容易扩展。十个待处理 task 就需要十次指派，lead 很快会成为瓶颈。

worker 完成一项工作后如果只能等待下一次派工，刚建立的 context 也无法继续利用。

另一种做法，是让 worker 自我组织并主动认领工作。

集中指派本身仍是可行设计，而且多数已发表的 multi-agent 研究讲的也是 manager 模式：每个子 agent 都注册成 tool，由 manager 分派 subtask。manager 掌握完整计划，因此能安排顺序、移除重复工作，也能提早结束整趟执行；代价是每个 task 都要经过 manager 发出与回收两次。

两种设计交换的是不同能力。manager 提供全局排序，但每个 task 都要排队等待；共享看板提高吞吐量，但得靠 lock，因为认领会过期。本章采用共享看板的做法。

自主机制必须让一个闲置的 agent 能够：

1. 察觉自己没事可做（work 阶段已抵达 `end_turn`）。
2. 查看共享看板，找出无人拥有、也没有被阻挡的 task。
3. 认领其中一个，且不与其他闲置 agent 相互竞争。
4. 针对认领到的 task 重新进入 loop，并持续重复直到看板清空。

少了 autonomy，每个 agent 都只能被动等待用户或 lead 送来下一个 prompt，整体吞吐量也会受限于派工速度。

---

## 核心机制

![机制图](assets/18-autonomy.png)

一个 outer loop 包住 agent loop。

inner loop 就是第 1 章那个普通的 `while`。当它抵达 `end_turn` 时，agent 不会返回，而是进入 poll。

poll 会把两个 channel 里的东西都收进来：一个定向的 inbox（第 16 章），承接寄给这个 agent 的消息；一个非定向的看板（第 12 章），放着任何闲置 agent 都能认领的 task。

它依优先序检查这些来源：先看 shutdown 请求，再看 inbox 消息，最后才看看板上的 task。

无论找到什么，都会成为下一个 prompt，接着 inner loop 再跑一次。

- inner loop 依模型的 `stop_reason` 结束，这与第 1 章是同一个信号。
- poll 先检查 shutdown，所以停止指令永远不会被 peer 消息淹没。
- 认领是在 lock 之下做「读取、检查、再写入」：挑一个无人拥有、未被阻挡的 task，然后在别的 agent 出手前写入拥有权。
- 只有当一个 task 的依赖项全都 `completed` 时它才可被认领，所以没有 agent 会认领被阻挡的工作。

shutdown 请求与其确认就是第 17 章的 protocol，所以停止走的是 handshake，不是强制中止。

### 本章添加：闲置 poll

`autonomy.py` 加上 outer loop 与一次 poll pass。`next_action` 把 inbox 收完一次，然后依优先序返回它找到的第一样东西：

```python
def next_action(proto, team, store, me):               # src/autonomy.py
    inbox = team.drain(me)
    shutdown = next((m for m in inbox if _is(m, "shutdown_request")), None)
    if shutdown is not None:                            # checked first, so chat cannot starve a stop
        proto.reply(shutdown, "shutdown_approved")
        return ("shutdown", shutdown["content"].get("reason"))
    chat = [m for m in inbox if isinstance(m["content"], str)]
    if chat:
        chat.sort(key=lambda m: m["from"] != "lead")   # lead before peers; sort is stable
        return ("message", _fold(chat))                # section 16's shared fold helper
    task = claim_next(store, me)                        # else claim the next ready task
    return ("task", task) if task is not None else None
```

- 它会返回三者中最先出现的：shutdown（确认后停止）、折叠后的 chat，或一个认领到的 task。
- shutdown 在 chat 之前检查，所以 peer 流量无法饿死一次停止（第 16 章）。
- `claim_next` 认领第一个 pending、无人拥有的 task；`TaskStore.claim` 会拒绝被阻挡的工作，并把认领序列化（第 12 章）。
- `None` 代表闲置：outer loop 睡一下再 poll 一次。

### 在 lock 之下认领

poll 提议一个 task；lock 决定谁拿到它。`claim_next` 由旧到新扫描看板，并提议第一个无人拥有、pending 的 task：

```python
def claim_next(store, me):                             # src/autonomy.py
    for t in store.list():                             # oldest first
        if t["status"] == "pending" and t["owner"] is None:
            got = store.claim(t["id"], me)             # read, check, write under a lock
            if got["ok"]:
                return got["task"]
            # not ok: another agent won it, or it just became blocked; try the next
    return None
```

`claim_next` 里的检查只是一个提示：两个闲置 agent 可能同时把同一个 task 读成无人拥有。`TaskStore.claim`（第 12 章）在 lock 之下做出裁决：

```python
def claim(self, tid, owner):                           # src/tasks.py, section 12
    with self._lock():                                 # fcntl.flock: one claimer at a time
        task = self.get(tid)
        if task["owner"] is not None:                  # someone already won: back off
            return {"ok": False, "reason": "already_claimed"}
        unmet = [b for b in task["blockedBy"]
                 if (self.get(b) or {}).get("status") != "completed"]
        if unmet:                                       # a dependency is not done yet
            return {"ok": False, "reason": "blocked"}
        task["owner"], task["status"] = owner, "in_progress"
        self._write(task)
        return {"ok": True, "task": task}
```

- lock 把读取、检查、写入包成一步做完，中间插不进别的 agent，所以检查不会在写入前过时。
- 落败者在 lock 内重新读取，看到 `owner` 已被配置，于是拿到 `already_claimed`；`claim_next` 便移到下一个 task。
- 被阻挡的 task 在这里同样会被拒绝，所以没有 agent 会认领依赖项尚未 `completed` 的工作。
- 这是唯一一处两条线程争用共享状态的地方。poll 的其余部分都是本地的。

### 如何集成到现有架构

outer loop 从外部包住 `run_turn`，所以 loop 与 subagent 路径都不变：

```python
def run_teammate(team, store, me, lead, work):         # src/autonomy.py
    proto, prompt, claimed = Protocol(team, me), None, None
    while True:
        if prompt is not None:
            work(prompt, claimed)                      # inner loop (section 1) does the claimed task
            prompt, claimed = None, None
            team.send(me, lead, {"type": "idle", "reason": "available"})
        action = next_action(proto, team, store, me)   # poll: shutdown, message, or task
        if action is None:                             # idle: sleep, then poll again
            time.sleep(POLL_INTERVAL); continue
        kind, payload = action
        if kind == "shutdown":
            return "shutdown"
        if kind == "task":
            prompt, claimed = task_prompt(payload), payload
        else:
            prompt = payload
```

- 这个 `run_teammate` 就是第 17 章的版本，只多一个 poll 来源：task 看板。shutdown（第 17 章）与 chat（第 16 章）都不变。
- `work(prompt, claimed)` 针对认领到的 task 跑一次 inner loop 到 `end_turn`，接着 agent 声明自己有空。
- 认领到的 task 成为下一个 prompt。当 poll 找不到任何东西时，worker 自己决定何时停止。
- 那个停止有两种模式：闲置直到完成 shutdown handshake（第 17 章），或在有限看板上跑满一定次数的空 poll 后收工。
- 这里只跑一个 worker，但 loop 是每个 agent 各一份。真正的团队会同时跑一个 lead loop 与多个 worker loop，共享同一组看板与 inbox。
- lead 只做一个主动步骤：它调用工具建立团队与工作，然后就结束了。
- `TeamCreate` 与 `SpawnTeammate` 是第 16 章的工具；`TaskCreate` 把 task 贴上看板（第 12 章）。
- `SpawnTeammate` 就是 `runtime.start(...)`（第 13 章）：lead 的工具调用会在一条线程上启动一个 worker 的自主 loop。
- spawn 之后，拉取工作与决定何时停止都是每个 worker 自己的事，lead 和外层程序都不介入。主进程只是等待 worker 收工。
- 组建团队、spawn、贴看板都是模型的决定（第 16 章与第 12 章）；自主认领则是第 18 章添加的部分。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book 和已发表的研究，也未经下面表格的系统证实。

**怎么问一个正在忙的 worker：**poll 只告诉 worker 下一步做什么，它从来不会告诉 lead 某个正在跑的 worker 现在怎么样。

**状态查询（status RPC）为什么很弱：**worker 正在跑一次 tool call 的时候，它根本没在听消息，所以这种调用不是卡住，就是返回空的。
真正卡死的那个 worker，刚好就是不会回你的那个。

**三种真的可行的做法：**第一种要 worker 配合，最后一种完全不用。

1. 用消息问。把一则状态请求丢进 inbox（第 16 章），worker 下次 poll 时就会回答。这很准，前提是 worker 还在 poll。
2. 读一个讲好的进度文件。worker 每做一步就往双方都知道的路径加一行，读的人完全不打断它干活。
3. 跟着读存下来的 trajectory。runtime 本来就会把每一轮写进磁盘（第 13 章），lead 直接读那些轮次，worker 什么都不用做。

**怎么看出它卡住了：**后两种做法顺便就给了答案。看文件最后一次写入是什么时候，没有新的写入就是没有新的进展。
再配一个门槛，这就能拿来下判断。慢的 tool call 跑起来同样不会写东西，所以门槛要设得比你预期最慢的调用还长。超过门槛，就当这个 worker 卡死了。
「卡在忙碌」这个失败模式缺的就是这个触发条件。有了门槛，lead 可以把 task 收回来，或是开一次 shutdown handshake（第 17 章），不用一直干等。

**帮 worker 池加上预算：**poll 里没有任何东西会叫 worker 别再认领了。每个闲下来的 worker 都会再拿一个 task，所以收工的时机是预算用完，而不是活做完。

**要分配的到底是什么：**有一个公开的 multi-agent 系统发现，光是 token 用量就解释了大约 80% 的表现差异。
所以真正要分配的是 token，不是轮次。有四个旋钮可以挂在看板和 worker 池上：

- 单一 task 的预算。每个 task 贴上看板时就写好自己的步数上限与 token 上限，这样一个失控的 task 吃不掉整场的资源。
- 并发上限。限制同时最多几个 task 停在 `in_progress`。看板本来就在算这个数，超过上限的认领直接失败就好。
- 模型配置。最强的模型放在最需要动脑的地方。计划的好坏决定结果，所以强模型给 lead，例行的 worker 用便宜的就好。
- 抢占（preemption）。超出预算的 worker，或是停摆超过门槛的 worker，手上的 task 会被收回看板。下一个认领的人从干净的状态开始。

**把预算摊给 worker 看：**知道自己手上剩多少的 agent，花起来会跟只是拿到更大上限的 agent 不一样。
这个结论出自书里自己做的实验，只有这一个来源。把它当成一个可以验证的方向，别当成可以照抄的数字。

---

## 不同系统怎么做

一个闲置 agent 如何找到并认领属于自己的工作。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **优点** | 没有派工者瓶颈。watcher 连别处建立的 task 也会接手。 | 无人看管的执行结果可预期，续跑状态也存得住。 |
| **限制** | 两个闲置 agent 可能盯上同一个 task，得靠一把锁裁定。 | 一个 agent 只顾一个 goal。做完了没，也是模型自己判断。 |
| **设计原因** | lead 逐一派 task 会成为瓶颈，所以让 worker 自我组织。 | 自主不是一种模式，而是一个有预算的权限等级。 |
| **做法：idle behavior** | 500ms 一轮的 poll：先查 shutdown，再看未读消息，接着认领。 | 整个 agent 闲下来时，先订走下一轮，再排一则 prompt。 |
| **做法：work claim** | 在锁之下写入没被阻挡的 task 的拥有权，只有一个人抢得到。 | 拿 goal 当下的版本号去订下一轮，版本对不上就订不到。 |
| **做法：self-organization** | worker 从看板拉工作（第 12 章）。lead 只做集成，不派工。 | 没有看板。agent 续跑自己的 goal，往外开的量也有上限。 |

---

## 常见问题

- **认领竞争（Claim race）：**两个 agent 把一个 task 读成无人拥有并双双认领，丢掉了其中一个 agent 的工作。在一个 file lock 内做认领，检查与写入一步做完，中间插不进别的 agent（第 12 章）。
- **被闲聊饿死（Starvation by chatter）：**peer 闲聊淹没了一个 shutdown 请求，于是一个该停止的 agent 继续 poll。在一般消息之前先检查 shutdown（第 16 章）。
- **过早认领被阻挡的工作：**一个 agent 认领了依赖项尚未完成的 task，然后卡住。跳过任何 `blockedBy` 仍含未解 id 的 task（第 12 章）。
- **compaction 后身分遗失：**一个长时间运行的 teammate 在执行途中被自动 compaction（第 8 章），忘了自己的角色。保留 system prompt，让角色得以存续。
- **卡在忙碌，或卡在闲置：**一个永远抵达不了 `end_turn` 的阶段永远不会释放；一个没有出口的 poll 会空转。依 stop 信号结束（第 1 章）；每次 poll 都检查 abort。
- **停摆没人发现：**一个 worker 抓着 task 看起来很忙，其实毫无进展，看板就一直不会把它放回去。看它的进度文件最后一次写入是什么时候，超时就把 task 收回来。
- **整池预算失控：**闲下来的 worker 一直认领，于是要等预算花光才收得了工。每个 task 都给步数与 token 上限，同时能跑几个也要设上限。
- **抢占后重复写入：**task 被放回看板又被别人认领时，旧的 worker 还在跑，于是两个 agent 写同一批文件。等停止被确认之后再放回去（第 17 章）。

---

## 动手跑跑看

[`src/`](src/) 承接第 17 章并加上：

- [`autonomy.py`](src/autonomy.py)：在第 12 章看板之上的 outer loop 与 idle poll（由第 16 章的 `SpawnTeammate` 启动每个 worker）。
- [`test.py`](src/test.py)：单一 worker 的机制、一个 `TeamCreate` 检查、一次强制的认领竞争（16 条线程、一个 task、一个赢家）、一条多线程 pipeline，以及一个 spawn 工具检查。
- [`demo.py`](src/demo.py)：lead 做一个步骤（`TeamCreate`、`TaskCreate`、`SpawnTeammate`）；接着 worker 从看板拉取 task，并在看板清空时自行停止。

机制段落里的单一 worker `run_teammate` 是教学用的简化版。

真正的团队会同时跑一个 lead loop 与数个 worker loop，共享同一组看板与 inbox。

第 13 章在线程上启动工作；第 12 章与第 16 章的 file lock 在竞争下保护共享状态的安全。

并行 demo 与认领竞争测试把这一切串起来。

```bash
python sections/18-autonomy/src/test.py         # offline checks, no key
uv run python sections/18-autonomy/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code autonomy](https://github.com/yasasbanukaofficial/claude-code)：
  `utils/swarm/inProcessRunner.ts`（`runInProcessTeammate`、`waitForNextPromptOrShutdown`、`findAvailableTask`、`tryClaimNextTask`、`sendIdleNotification`）。
- [Claude Code claim and watch](https://github.com/yasasbanukaofficial/claude-code)：
  `utils/tasks.ts`（`proper-lockfile` 之下的 `claimTask`、`claimTaskWithBusyCheck`）、`hooks/useTaskListWatcher.ts`、`coordinator/coordinatorMode.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/bundle/headless/README.md`、`packages/goal/goal-round-driver/README.md`、`packages/workflow/tool-ralph/README.md`、
  `docs/subsystems/permission-presets.md`、`docs/subsystems/goal.md`。
- [learn-claude-code · s17 autonomous agents](https://github.com/shareAI-lab/learn-claude-code)：章节定位。
- [ai-agent-book · 第 10 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md)（《深入理解 AI Agent》，李博杰，以中文原版为准）：
  为什么主动去拉的状态查询很弱、进度文件与跟读 trajectory、用 mtime 判断停摆、manager 模式的集中指派，
  以及团队层级的资源调度（每个 subtask 的预算、并发上限、模型配置、抢占）。
  其中「让 agent 知道还剩多少预算」的结论出自书里自己的实验，只有这一个来源。
- [Plan-and-Act](https://arxiv.org/abs/2503.09572)（Erdogan 等，2025）：把 planner 和 executor 拆开，并指出计划质量才是决定结果的那一项。
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)（Anthropic，2025）：
  光是 token 用量就解释了大约 80% 的性能差异，其次才是 tool call 次数与模型选择。
