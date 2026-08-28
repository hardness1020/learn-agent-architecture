# 16 · Coordination

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> lead 依任务规模块成团队，让每位 agent 在独立 thread 上工作，再通过 inbox 协作。

单一 agent 只有一个 context window，同一时间能处理的工作也有限。面对大型任务，往往需要多个 agent 同时进行。

subagent 适合处理范围明确的子任务，但一次性的 subagent 启动后，很难在执行途中持续沟通或调整方向。

每增加一个 agent，就会增加 token 成本，也可能让多个 agent 对同一个文件做出互相冲突的修改。
因此，第一个要解决的不是如何 spawn，而是团队结构：需要几个 agent、是否共享 context，以及谁负责指派工作。

要让多个 agent 真正协作，系统必须提供稳定的身分、spawn 机制、可收发消息的 inbox，以及把权限请求送回用户的管道。

协调必须：

1. 为每个 agent 提供稳定、可寻址的身分。
2. 让 lead 依任务规模决定团队结构。
3. 让每位成员在自己的 thread 上执行。
4. 让成员主动读取 inbox 并采取行动，不必由 harness 逐步控制。
5. 将需要批准的动作往上转交，直到用户做出决定。

少了 coordination，大型工作只能按顺序处理，或拆成一群彼此无法沟通的 worker。

---

## 核心机制

![机制图](assets/16-coordination.png)

每个 agent 都有自己的 inbox。传送消息时，内容会写进收件者的 inbox；等收件者主动读取时，消息才会进入它的工作流程。

团队需要几个人、各叫什么名字，会由 lead 的 LLM 在执行时根据任务决定，而不是写死在程序里。lead 先调用 `TeamCreate` 组成团队，再 spawn 每一位成员。

lead 不会亲手启动队友。它调用 `SpawnTeammate`，由 harness 在后台 thread 上跑队友的 loop（第 13 章）。
队友接着拉取自己的 inbox 并行动，没有任何程序在逐步驱动谁。

demo 里没有中央 broker。有的是名字、inbox 路径与消息格式的共享惯例。

- 每个 agent 拥有一个 inbox。
- 一则消息有 sender、recipient 和 content。
- lead 调用 `TeamCreate` 决定名单的规模与组成；`SpawnTeammate` 再启动每位成员。
- lead 用 `SpawnTeammate` spawn 一个队友；那个队友在自己的 thread 上运作。
- `to="*"` 会 broadcast 给除了 sender 以外的每一位队友。
- sender 写完就返回。它们不会 block 等待回复。
- 队友每次 poll 都会读自己的 inbox，把新消息并入下一个 turn。
- 权限请求走同一个管道。

### 本章添加：组成团队

`TeamCreate` 是 lead 调用来决定名单规模与组成的工具。它填入一个单槽的 holder，harness 在 spawn 每位成员时读回：

```python
def team_tools(root, me, formed):                      # src/mailbox.py
    def create(a):
        members = list(dict.fromkeys([me, *a["members"]]))   # the lead joins its own team
        formed["team"] = Team(root, members)                 # the tool call sizes and forms the team
        return f"team created: {', '.join(members)}"
    ...                                                # SendMessage stays inert until the team exists
```

- 规模和名字都没有写死在程序里；两者都由 lead 的 LLM 依任务挑选。
- `SendMessage` 在 `TeamCreate` 执行前不会生效，所以 lead 必须先组成团队才能发送消息。
- `formed` 是一个单槽的 holder（ponytail：一个 in-process 的团队登记表替身；可以用一个名单文件作为后端，让另一个 process 的队友加入）。

### 本章添加：spawn 一个队友

`SpawnTeammate` 是 lead 的模型调用的工具。harness 在第 13 章的 runtime 上、在自己的 thread 上启动队友的 loop：

```python
def teammate_tools(runtime, spawn_worker):             # src/mailbox.py
    def spawn(a):
        runtime.start(lambda: spawn_worker(a["name"]))  # section-13 thread runs the teammate's loop
        return f"spawned teammate {a['name']}; it runs on its own thread and pulls its own work"
    return [Tool("SpawnTeammate", spawn, is_read_only=True, ...)]
```

队友的 loop 是 `serve_mailbox`：拉取 inbox、行动、重复。它在被 spawn 出来的 thread 上运作，所以队友是自己对消息做反应，不是被程序排好每一步：

```python
def serve_mailbox(team, me, work, *, poll=0.05, max_idle_polls=None):   # src/mailbox.py
    while True:
        chat = [m for m in team.drain(me) if isinstance(m["content"], str)]
        if chat:                                        # a message to act on
            folded = "\n".join(f"<message from={m['from']!r}>{m['content']}</message>" for m in chat)
            work(folded)                                # one inner loop (section 1) on the message
            continue
        time.sleep(poll)                                # empty: poll again
```

- `spawn_worker(name)` 是应用端的 thunk；它为那个队友跑一个 `serve_mailbox` loop。
- 队友在 drain 时就把消息拿走，所以一则消息只会被收到一次。
- 目前还没有优雅的停止方式。thread 是一个 daemon，会随 process 一起死掉。第 17 章加入 shutdown handshake。
- `max_idle_polls` 为闲置等待设上界，好让 demo 或 test 结束；真正的队友会一直 poll，直到 process 停止。

### inbox 与权限管道

context 各自独立的 agent 只有两种讲话的方式，跟 process 之间的那两种一样。
shared memory 是大家读写同一个地方，看到的状态是同一份。message passing 是 sender 把一份副本寄给指定的 receiver，两边没有共享任何东西。
承载这两种的管道有三条。工具调用的参数只有单向，没有回话的路。文件重启动也还在，但需要 lock。
message bus 多了地址与顺序信息，但只有持久化到磁盘后，才能在重启后继续使用。
这里的 inbox 是一个受 lock 保护的文件，因此本质上是在共享文件系统上实现 message passing。
team memory（第 9 章）和 task 看板（第 18 章）则是 shared memory 那一边。
大部分团队两种都要：用消息把工作发下去，用 shared memory 放那些比一则消息活得更久的事实。

`mailbox.py` 实现一个由命名 inbox 组成的 `Team`：

```python
def send(self, frm, to, content):                      # src/mailbox.py
    targets = [m for m in self.members if m != frm] if to == "*" else [self._check(to)]
    with self._lock():                                 # serialize concurrent senders
        for t in targets:
            inbox = self._read(t)
            inbox.append({"from": frm, "to": t, "content": content})
            self._path(t).write_text(json.dumps(inbox))
```

- `_check` 在未知名称变成路径之前就拒绝它。
- lock 把 read-modify-write 序列化，所以并行的 sender 不会漏掉消息。
- `drain` 读取并清空一个 inbox。

permission bubbling 是一种 approver 的实现。它把有闸门的调用通过同一个管道搬给用户：

```python
def bubbling_approver(team, me, lead, human=None, timeout=0.0, poll=0.05):
    def approve(name, args):                            # approver for an agent with no human UI
        team.send(me, lead, {"kind": "permission_request", "tool": name, "args": args})
        if human is not None:                           # the lead routes it to its approval UI
            team.send(lead, me, {"kind": "permission_response", "tool": name, "ok": human(name, args)})
        deadline = time.time() + timeout
        while True:
            resp = [m["content"] for m in team.drain(me)
                    if isinstance(m["content"], dict) and m["content"].get("kind") == "permission_response"]
            if resp:
                return bool(resp[-1]["ok"])
            if time.time() >= deadline:
                return False                            # nobody answered in time: default deny
            time.sleep(poll)
    return approve
```

1. 队友碰到一个有闸门的工具调用，但它自己的 loop 前面没有用户可以问。
2. approver 把一则 `permission_request` 送到 lead 的 inbox。
3. lead 把它导向自己的审核 UI（这里是 `human` callback）。
4. 裁决以 `permission_response` 的形式回到队友的 inbox。
5. 队友读取那则回复，把 allow 或 deny 返回给闸门。

闸门仍然调用 `approver(name, args)`，没有改变。答案以 inbox 消息而非直接调用的形式抵达，所以升级重用了同一个管道。

没有 `human` 时，答案必须来自别处（另一条 thread 上的 lead，或聊天平台上的一个人）。
approver 会 poll 自己的 inbox 直到 `timeout`，然后 deny：没有人回答的权限就是不行，绝不是卡住或放行。
这对应 Hermes 的 clarify gateway：`wait_for_response` 会 block 住 agent thread，直到聊天 adapter 回答或 timeout 到期。

### 如何集成到现有架构

demo 跑一个主 agent。lead 走一步，队友就自己运作起来：

```python
def spawn_worker(name, formed, model):                 # src/demo.py, module level
    team = formed["team"]                              # whatever the lead formed with TeamCreate
    ...                                                 # build the teammate's tools
    return mailbox.serve_mailbox(team, name, work)      # the teammate pulls its own inbox

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
```

- 程序唯一写死的输入是 lead 的目标。lead 用 `TeamCreate` 决定团队规模、用 `SpawnTeammate` spawn 每一位、用 `SendMessage` 委派。
- `demo()` 跑一个 `run_turn`，也就是 lead 的。队友自己的 `run_turn` 位于 `spawn_worker`，只能通过 spawn 工具抵达。
- 每个队友在第 13 章的 thread 上跑 `serve_mailbox`：拉取 inbox、工作、回复。回复数量由 lead 决定；主 process 只是等待。
- `loop.py` 维持通用。折叠与拉取 loop 属于协调，在这个 wrapper 里完成，不在 `run_turn` 内部。
- 权限闸门没有改变；有闸门的调用仍会往上转给 lead 审核。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book 和公开的多 agent 研究，也未经下面表格的系统证实。

**什么时候一个团队会赢过单一 agent：**只有当第二个 agent 能带回第一个看不到的东西，才值得多加一个。
一份测试结果、一张截图、一个抓回来的网页、一个从运行中的系统问到的答案。这些叫新信息。
如果多个 agent 只是重读同一份文字再投票，并不会产生新信息，只会增加 token 成本。

有两份公开的结果讲出了做错的代价。Tran 和 Kiela 给单一 agent 和一个团队同样的 thinking token 预算，在他们测的那些任务上，单一 agent 跟得上。
Anthropic 则说他们的 research 团队会用掉大约单次对话 turn 十五倍的 token。这么贵的团队总得带回点什么。

**context 是共享还是隔离：**两个 agent 要么共享一份历史，要么各留各的：

- **共享：**下一个 agent 直接接手全部，什么都不用打包，也不会有事实掉在半路。
  代价是同一时间只有一个 agent 在跑，而且一个 window 要装下整个团队的历史。
- **隔离：**每个 agent 有自己的 window，需要什么就得讲出来。大家可以同时跑，某个 agent 想歪了也停在自己的 window 里。
  代价是每一次交接都得写下来。

子任务不多、历史装得进一个 window、步骤本来就得照顺序跑，那就选共享。其他情况就选隔离。
这个 repo 走的是隔离：subagent 每次都从空的开始（第 6 章），队友只读自己的 inbox。

**三种拓扑：**就算 context 隔离了，还是得决定谁跟谁讲话。形状有三种：

- **对等：**地位相同的 agent 互相传消息。互审和交叉检查适合这种。
- **管理者：**一个 lead 把工作拆开、发出去、再把回来的结果合起来。子代返回的是摘要，不是自己的历史。
- **去中心化：**没有 lead。每个 agent 自己决定下一棒交给谁。

本章做的是管理者。lead 帮所有人规划，所以计划拆错了就是错了，下游没有 worker 补得回来。
这就是为什么最强的模型要给 lead，worker 用便宜的就好。

**去中心化的团队怎么把工作送到人手上：**没有 lead，工作还是得找到下一个 agent。三种公开的设计，三条路：

- **MetaGPT：**把每则消息丢进一个 pool。每个角色订阅自己处理的消息类型，所以 sender 从来不用指名 receiver。
- **AutoGen：**group chat 只留一份对话记录，由中央的 selector 决定下一个谁讲话。selector 要是一直挑同样两个 agent，这场对话就 livelock 了。
- **OpenAI Swarm：**把每次交接做成一次工具调用，并限制工作最多能转手几次，这样一连串交接一定会停下来。

**文件树的四个区域：**agent 之间靠名字找到对方，找状态则是靠路径。书上把这棵树分成四个区域：

- **私有 scratchpad：**放一个 agent 自己的草稿。别人不会读，所以完全不用协调。
- **共享工作区：**放 repo、task 看板和 team memory。每个队友都会往这里写，冲突就是在这里发生的。
  这里需要 lock，或者一人一个 worktree（第 15 章）。
- **外部挂载：**放的是团队自己没做出来的东西，例如一份 checkout 或一份数据集。往这里写，等于动到了团队外面的东西。
- **只读的内置内容：**放 skill、prompt 和工具定义（第 7 章与第 2 章）。它们在整个执行期间都不会变，所以每个 agent 看到的都一样。

状态放错区域，最后会变成协调的 bug。两个 agent 同时改一个文件，代表那个文件放在共享工作区。
同一个事实传了三次，代表它本来就该写进 team memory。

**一次 handoff 要带什么：**队友看不到 lead 的对话，所以「去把坏掉的测试修好」这句话它根本无从下手。一个 handoff 包裹要带三样东西：

1. 任务本身，附上接收的人自己就能检查的验收标准。
2. 已经确认过的事实和要遵守的限制，这样接收的人不会再查一次，也不会踩过去。
3. 文件、log 和 branch 的路径。

sender 的原始历史不放进去。那东西很长、里面都是走不通的路，还会逼接收的人把 sender 犯过的错再读一遍。

另一个做法是共享 context 的交接，它整个跳过包裹。一个 agent 把控制权交给另一个，整段历史跟着过去，什么都不会漏掉。
书上用一个在角色之间转移控制权的工具示例这件事。那是作者自己做的实验，当成单一来源看就好。
代价是同一时间只有一个 agent 握着控制权，所以什么都没办法同时跑。写包裹要花工夫，换来的是可以同时进行的工作。

> **接下来：** 这里的队友是一个没有优雅停止方式的 daemon，而且它只对消息做反应。
> 第 17 章加入 shutdown handshake，好让 lead 能干净地结束一个队友。
> 第 18 章加入一块共享的 task 看板，让闲置的队友自己认领工作，而不是等着被传消息。

---

## 不同系统怎么做

一种设计如何 spawn 出协作的 agent 并把工作分散给它们。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | 队友能直接交谈，文件 inbox 还能跨 process 或机器。 | 子代可以从任何已连接的接口暂停、中断。 | 一支脚本就能在硬性上限之下开出大量子代。 |
| **限制** | 文件 inbox 有 poll 和 lock 成本，内存 inbox 随 process 死。 | 没有对等 inbox，clarify 还会卡住自己的 thread。 | 子代彼此不能讲话，送消息也不会有回复。 |
| **设计原因** | 队友彼此对等，需要 inbox 交谈，也需要一条送回人的路。 | 协调维持 parent 对 child。 | 协调就是归属关系，每个子代只有一个 parent。 |
| **做法：teammates** | in-process 或 remote，各自跑自己的 loop。 | thread 上的委派子代，有暂停标志。 | 由模型写的脚本开出子代，长命的那种会常驻。 |
| **做法：channel** | SendMessage 写进 inbox，也能 broadcast。 | completion queue 加 gateway RPC。 | 只有 parent 对 child。子代用 report 工具回话。 |
| **做法：shared memory** | team task list 与团队 memory 目录。 | 共享的 session DB，外加 lineage 标记。 | parent 的工作目录。fork 还会复制它跑完的 turn。 |
| **做法：permission bubbling** | remote 权限请求转成本地的审核提示。 | clarify 导向聊天平台，子代自动 deny 或 approve。 | 权限请求沿着 parent 这条线往上问。 |

---

## 常见问题

- **遗失消息的竞态：**两个 sender 同时写一个 inbox。用 lock 保护 read-modify-write。
- **对等 deadlock：**agent 互相等待。把消息排入队列并在 turn 之间 drain，而不是用会 block 的传送。
- **权限卡住：**队友没有 UI 可以问用户。把请求往上转给 lead 代问。
- **create 之前就 spawn：**lead 在 `TeamCreate` 之前就 spawn 或传消息，于是没有名单。让两者在团队存在之前都保持无作用。
- **孤儿队友：**被 spawn 的队友在工作做完后还一直 poll。为闲置等待设上界，或用第 17 章的 handshake 停止它。
- **含糊的跨 agent 消息：**队友看不到 lead 的对话。改成送一个包裹：任务、验收标准、已确认的事实、产出物路径。
- **把 chat 当 memory 用：**耐久的共享事实属于 team memory。
- **拜占庭式的队友：**坏掉的 agent 不会 crash。它会回一个错的答案，而且讲得很笃定。
  重试它，或拿同一份证据去投票，拿回来的还是同一个答案。只有拿模型以外的东西去对，才抓得到。
- **共享文件的更新被盖掉：**两个 agent 读同一个文件，然后各自写回去，先写的那笔就没了。
  写入时上 lock，或者存一个版本号，对不上就重试。
- **语义冲突：**两边的写入都干净地套用了，结果还是坏的。一个 agent 把某个函数改了名字，另一个 agent 同时照旧名字加了调用。
  把工作拆开，别让两个 agent 管到同一件东西，或者只在一个点上合并。
- **错误级联放大：**一个 agent 把某个事实搞错了。下一个 agent 照抄，再下一个又照抄，到后来看起来就像已经确认过的事。
  只看结论的审查者会觉得前后一致。要找人去对原始证据，而且不能找产出它的那个 agent。

---

## 动手跑跑看

[`src/`](src/) 承接第 15 章并加上：

- [`mailbox.py`](src/mailbox.py)：具 locking 的命名 inbox、折叠、`serve_mailbox` loop、带 timeout 与默认 deny 的 bubbling，以及团队工具。
- [`test.py`](src/test.py)：检查定址、broadcast、并行传送、折叠、bubbling（inline、异步与 timeout-deny）、mailbox loop，以及团队工具。
- [`demo.py`](src/demo.py)：lead 走一步（`TeamCreate`、`SpawnTeammate`、`SendMessage`）；每个队友拉取自己的 inbox、跑一个有闸门的 shell 任务，然后回报。

loop 与 subagent 路径不变。协调通过 spawn 队友、drain inbox、传入一个 approver 来包住 turn。

```bash
python sections/16-coordination/src/test.py         # offline checks, no key
uv run python sections/16-coordination/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 工具与 inbox](https://github.com/yasasbanukaofficial/claude-code)：`tools/SendMessageTool/`、`tools/TeamCreateTool/`、`utils/mailbox.ts`、`utils/teammateMailbox.ts`。
- [Claude Code 队友](https://github.com/yasasbanukaofficial/claude-code)：
  `tasks/InProcessTeammateTask/`、`tasks/RemoteAgentTask/`、`remote/remotePermissionBridge.ts`、`memdir/teamMemPaths.ts`。
- [Hermes Agent 源代码](https://github.com/NousResearch/hermes-agent)：`tools/delegate_tool.py`、`tools/async_delegation.py`、`tools/clarify_gateway.py`、`tools/interrupt.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/workflow.md`、`docs/subsystems/subagent.md`、`docs/subsystems/core.md`、
  `packages/workflow/workflow-worker-thread/README.md`、`packages/subagent/tool-subagent-report/README.md`。
- [learn-claude-code · s15_agent_teams](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter10.md`（多 Agent 协作），以中文原文为准。
  context 共不共享、拓扑分类、文件系统分区、handoff 包裹。角色互转那个示例是作者自己的实验。
- Cemri et al., *Why Do Multi-Agent LLM Systems Fail?*（[arXiv:2503.13657](https://arxiv.org/abs/2503.13657)）：MAST 分类法与拜占庭式的框架。
- Tran, Kiela, *Single-Agent LLMs Outperform Multi-Agent Systems Under Equal Thinking Token Budgets*（[arXiv:2604.02460](https://arxiv.org/abs/2604.02460)）。
- Erdogan et al., *Plan-and-Act*（[arXiv:2503.09572](https://arxiv.org/abs/2503.09572)）：planner 的质量就是这次执行的上限。
- Anthropic, [*How we built our multi-agent research system*](https://www.anthropic.com/engineering/multi-agent-research-system)：一个 research 团队的 token 成本。
- [MetaGPT](https://arxiv.org/abs/2308.00352)、[AutoGen](https://arxiv.org/abs/2308.08155)、[OpenAI Swarm](https://github.com/openai/swarm)：去中心化的路由与交接次数上限。
