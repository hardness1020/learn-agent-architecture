# 17 · Protocols

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md)

> 给消息一份合约：先批准再行动，先确认再停止。

第 16 章的 coordination 提供了沟通管道，但管道只能传递文字。若没有额外规则，系统无法分辨请求与回复，也不能保证 agent 会先等待批准再行动。

protocol 是建立在通讯管道上的共同约定，用来定义请求与回复的格式，以及每则回复要如何对应到原始请求。

这套规则在两种情况特别重要。第一种是 lead 强制停止正在编辑的队友，留下只写到一半的文件和未关闭的 task。第二种是队友没有先取得批准，就直接执行高风险重构，等做完才回报。

两种情况需要的核心机制相同：一方送出请求，另一方明确回复，再用同一个 id 把两者关联起来。

protocol 必须：

1. 为请求与回复定义固定格式。
2. 将每则回复对应到正确的原始请求。
3. 在高风险工作开始前先取得批准。
4. 停止 agent 时保留尚未完成的工作。
5. 多个 worker 竞速时，第一个完成者能结束整批工作，而且只结算一次。
6. 与团队外部的 agent 沟通时，仍能跨越信任边界安全运行。

少了 protocol，coordination 就只是 agent 彼此传文字。高风险动作没有批准机制，停止时无法完整收尾，收到回复后也不知道它对应哪个请求。

---

## 核心机制

![机制图](assets/17-protocols.png)

每一次来回都是一则带类型的请求，配一则带类型的响应，两者共享同一个 `requestId`。

sender 把请求记为 pending，依类型路由回复，并解析出相符的请求。

有三条规则让它成为一个 protocol，而不只是两则消息：

- **Typed variants：**每则消息是 `type` 字段上的一个 variant。handler 依类型 dispatch，所以回复绝不会被误认为某个不相干的请求。
- **Correlation id：**`requestId` 在请求送出时设置，并在回复里返回。sender 就知道一则回复解析的是哪一条 pending 请求。
- **A small state machine：**一条请求从 `pending` 走到 `approved` 或 `rejected`。一个 id 有了结果之后，再收到的回复都会被忽略，所以同一则回复重复送也没关系。

shutdown 与 plan 这两个流程一样，只是方向相反：shutdown 是 lead 请求、队友确认；plan approval 是队友请求、lead 确认。

审核的回复里也可以附上这件工作要用哪种权限模式（第 3 章），批准和模式一次送到。

### 本章添加：protocol 追踪器

`protocols.py` 是每个 agent 在第 16 章管道之上的一个 `Protocol`。一条请求铸造一个 correlation id 并把自己记为 pending；回复把那个 id 返回：

```python
def request(self, to, kind, **fields):                 # src/protocols.py
    self._n += 1
    rid = f"{self.me}-{self._n}"                        # per-sender id: unique, deterministic
    self.pending[rid] = {"kind": kind, "state": PENDING}
    self.team.send(self.me, to, {"type": kind, "request_id": rid, **fields})
    return rid

def reply(self, msg, kind, **fields):                  # echo the id back, do not mint a new one
    req = msg["content"]
    self.team.send(self.me, msg["from"], {"type": kind, "request_id": req["request_id"], **fields})
```

- `request` 把每个 id 编号为 `me-N`，所以 id 对每个 sender 都唯一，且跨 agent 绝不冲突。
- `reply` 重用请求的 `request_id`。那个返回就是整个诀窍所在：sender 之后就是靠它把回复对应到它所回答的内容。

一张小表指明哪些回复种类可以回答每种请求，以及各自代表的裁决：

```python
_REPLIES = {                                           # src/protocols.py
    "shutdown_request": {"shutdown_approved": APPROVED, "shutdown_rejected": REJECTED},
    "plan_approval_request": {"plan_approval_response": None},   # None: the verdict rides an `approved` field
}
```

`resolve` 读这张表，用来拒绝不相符的回复，而且裁决只记录一次：

```python
def resolve(self, msg):                                # src/protocols.py
    reply = msg["content"]
    req = self.pending.get(reply.get("request_id"))
    if not req or req["state"] != PENDING:             # unknown id or already resolved
        return None
    verdicts = _REPLIES[req["kind"]]
    if reply.get("type") not in verdicts:              # type-confusion guard
        return None
    state = verdicts[reply["type"]]
    if state is None:                                  # single-response flow carries the bool
        state = APPROVED if reply.get("approved") else REJECTED
    req["state"] = state
    return state
```

- `resolve` 是 idempotent 的：重复或不相干的回复会撞上 `state != PENDING` 或未知 id 的守卫，并返回 `None`。
- `verdicts` 查表就是 type-confusion 守卫：一则 `plan_approval_response` 无法解析一条 `shutdown_request`，因为那个类型不在 shutdown 那一列里。
- shutdown 把它的裁决拆到两个回复种类；plan approval 用一个携带 bool 的种类。两者都落到同一个从 `pending` 到 `approved` 或 `rejected` 的状态。
- `protocol_tools` 把 handshake 的发起作为工具暴露出来（`ExitPlanMode`、`ApprovePlan`、`StopTeammate`）。
- 确认一个 shutdown 不是一个工具；队友的 `run_teammate` loop 会自动回复（harness 驱动的接收）。

### 本章添加：队友 loop

`run_teammate` 是第 16 章的 `serve_mailbox`，把 shutdown handshake 折了进来。被 spawn 的队友现在会因为一条请求而停止，而不是随它的 daemon thread 一起死掉：

```python
def run_teammate(team, me, lead, work, *, poll=0.05, max_idle_polls=None):   # src/protocols.py
    proto = Protocol(team, me)
    while True:
        inbox = team.drain(me)
        shutdown = next((m for m in inbox if _is_shutdown(m)), None)
        if shutdown is not None:
            proto.reply(shutdown, "shutdown_approved")     # confirm, then stop
            return "shutdown"
        chat = [m for m in inbox if isinstance(m["content"], str)]
        if chat:
            work(_fold(chat)); continue                    # section 16: fold and run
        time.sleep(poll)                                   # empty: poll again
```

- shutdown 在 chat 之前先检查，所以一般消息不会把停止请求挤到后面。
- 发起是模型驱动的（lead 的 `StopTeammate`）；接收是 harness 驱动的（loop 确认），对应参考实现的分工。
- loop 返回 `"shutdown"`，所以进行 spawn 的 runtime（第 13 章）能回报这次干净的停止。
- 第 18 章再加一个分支：inbox 为空时，从一块共享看板认领一个 task。

### 如何集成到现有架构

demo 跑一个主 agent。lead 在一个 turn 里 spawn 一个队友、委派、然后停止它；队友在自己的 thread 上确认：

```python
def spawn_worker(name, team, model):                   # src/demo.py, module level
    ...                                                 # build the teammate's tools
    return run_teammate(team, name, "lead", work)       # serve_mailbox plus the shutdown handshake

run_turn([...goal...], model, lead_reg, session)        # the one agent call in demo(): the lead
state = next(filter(None, (lead_proto.resolve(m) for m in team.drain("lead")   # -> approved
                           if isinstance(m["content"], dict))), None)
```

- `demo()` 跑一个 `run_turn`，也就是 lead 的。它调用 `SpawnTeammate`、`SendMessage`，然后 `StopTeammate`。
- `StopTeammate` 送出一条 `shutdown_request`；队友的 `run_teammate` 确认它并返回。这次停止走的是 handshake，不是直接 kill。
- lead 把返回的 `shutdown_approved` 解析成 `approved`。主 process 只是等待。
- plan-approval 流程就是同一套 handshake 反过来跑（先 `ExitPlanMode` 再 `ApprovePlan`），由相同的工具驱动，并在 test.py 里验证。
- loop 没有改变。protocol 在管道上把请求塑形、把回复结案，这样包住一次 turn。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book 和 A2A 规格，也未经下面表格的系统证实。

**一次停掉一整批：**同一件事派好几个 worker 去做，但只要一个答案。
第一个做成功的 worker 回报上来，lead 就对其余每个 worker 各送一则停止。
demo 只停过一个队友，不过线路上传的东西没有变。每一则停止都还是那套先请求再确认，
所以没抢到的 worker 一样会把文件写完、把 task 记录收掉。就是 shutdown 流程一次送给很多人。

**同一瞬间两个都赢：**两个 worker 有可能同时做完。这时两个都算第一名，lead 会送两轮停止，结果也记成两笔。
加一把锁就好了。先到的那个拿到锁，写下谁赢了，再放掉。
第二个接着拿到锁，看到已经有人写了，就直接返回，不再去停任何人。不管谁先到，这件事都只结算一次。

**确认一直不回来：**等确认的停止有可能没人回。worker 卡在一个很久的 tool call 里，根本没在读 inbox。
所以停止分成两层：lead 先问，等确认等到一个期限，期限一到就把还在跑的直接砍掉。
砍掉是备用方案，不是第一步。lead 一定先问，所以只要还来得及收尾，收尾就会跑。

**只有单一来源：**这两层和那把锁都出自书作者自己的一个实验，不是比较过好几个系统之后的结论。

**跟不是自己家的 agent 讲话：**上面讲的都假设是同一个团队、同一个 process、同一个拥有者。
管道是共享的，成员名单在 spawn 时就知道，agent 之间也都信得过线路上的那些 id。
一跨出组织，这些就全都不成立了。没有共享的 inbox 可以盖 `request_id`。对方有哪些成员看不到。对方的工具列表也不能直接信。
A2A 就是为这种情况设计的 protocol。它保留请求配回复这个核心，另外加三样东西。

- **Agent Card discovery：**每个 agent 在一个固定的 URL 上放一份文件：名字、会做什么、endpoint，还有要怎么认证。
  调用方先读这张卡，再决定要送什么过去。团队里的名单在 spawn 时就拿到了；跨出去就得自己去抓。
- **Task lifecycle：**一次远程调用是一个带 id 的 task，状态有 `submitted`、`working`、`input-required`、`completed`、`failed`。调用方拿这个 id 去轮询或订阅。
  `input-required` 正好是本章没有名字的那个状态：对方停下来要更多信息，而 task 在等的期间还活着。
- **Opaque artifacts：**结果是以 artifact 返回的：文件、文字、结构化片段。对方的 trajectory 不会返回。
  调用方看不到那边是怎么做出来的，过得来的只有结果。

**请求的状态和 task 的状态：**两套做法记的东西不一样。本章记的是一次请求：从 `pending` 走到 `approved` 或 `rejected`。
A2A 记的是一个 task：`submitted`、`working`、`input-required`、`completed`、`failed`。
差别在这笔记录活多久。请求的记录跟着那次来回一起结束。
task 的 id 之后还查得到：回复收到之后、中途停下来要信息之后、连接断掉又接回来之后，都还查得到。
跨边界的调用方两种都要留。请求状态讲的是这一则消息对方收不收，task 状态讲的是整件事现在做到哪。

---

## 不同系统怎么做

一种设计如何定出请求的格式、为计划设闸门，并干净地停止 agent。

| | Claude Code | deepseek-harness |
| --- | --- | --- |
| **优点** | 每一次停止都经过确认，有风险的计划都设了闸门。 | 只要照公开协议讲话，任何 client 或 server 都能接上来。 |
| **限制** | 每次 handshake 都要付出往返次数和 protocol 状态。 | 输出要等到定案才送出，中途的进度看不到。 |
| **设计原因** | 编辑到一半被强制停掉，会留下写一半的文件。有风险的计划也该先审核。 | 对面是一个你未必拥有的 process，所以用公开契约讲话。 |
| **做法：message shape** | 在 `type` 上区分的 typed union，`request_id` 对应每则回复。 | 用 session id 分辨的 JSON-RPC 方法，一个 session 同时只跑一个 prompt。 |
| **做法：plan approval** | 队友请求后等待，lead 的回复带着裁决、feedback 和权限模式。 | 计划送到人面前。被打回来时，会以带着意见的失败调用返回。 |
| **做法：shutdown** | lead 先请求，队友确认后才 kill。 | 先取消、再关掉输入、再送 signal、最后强杀，每一阶都有时限。 |

---

## 常见问题

- **用硬 kill 代替 handshake：**杀掉队友的 thread 会丢掉进行中的工作，并让它的 task 记录变孤儿。改用先请求再确认、并把 task 标记为 `notified` 的流程。
- **孤儿请求：**一则永远不到的回复会让一条请求永远停在 `pending`，于是 sender 一直 block。加上一个 timeout 或闲置检查，把卡住的请求浮上来。
- **类型混淆：**只靠 id 对应回复，会让一则 shutdown 回复解析掉一条 plan 请求。检查回复的 variant 是否符合记录下的请求类型。
- **审核却不强制：**一个被审核通过的计划，仍需要权限层来为执行设闸门（第 3 章）。在响应里携带 `permissionMode`。
- **重复回复：**一则重送的回复可能把已经定案的状态翻掉。任何针对非 pending id 的回复都不做事。
- **停一整批却没有锁：**两个 worker 同一瞬间做完，两个都算第一名，lead 就送了两轮停止，结果也记成两笔。
  写下谁赢之前先拿锁。晚一步的赢家看到名字已经在上面，就谁也不停。
- **等确认却没有期限：**worker 忙在一个很久的 tool call 里，根本没读到请求。确认一直不来，lead 就一直等下去。
  等待要设期限，超过就直接砍。先问还是第一步，只是不再是唯一一步。
- **把远程 task 当成一则回复：**不是自己家的 agent 会停下来要更多信息。那是一个 task 状态，不是一则回复。
  记下 task id 和它的状态。起头那次来回结束之后，停住的工作还找得回来。

---

## 动手跑跑看

[`src/`](src/) 承接第 16 章并加上：

- [`protocols.py`](src/protocols.py)：请求追踪器（typed variants、correlation id、状态机）、handshake 工具，以及 `run_teammate` loop。
- [`test.py`](src/test.py)：检查 shutdown 与 plan 流程、各个守卫、一次工具驱动的 handshake，以及一个被 handshake 停止的自运行队友。
- [`demo.py`](src/demo.py)：一个 lead turn spawn 一个队友、委派，并用 StopTeammate 停止它；队友在自己的 thread 上确认。

loop 与 subagent 路径不变。protocol 在管道上把请求塑形、把回复结案，这样包住一次 turn。

```bash
python sections/17-protocols/src/test.py         # offline checks, no key
uv run python sections/17-protocols/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 的 protocol 格式](https://github.com/yasasbanukaofficial/claude-code)：`tools/SendMessageTool/SendMessageTool.ts`、`utils/teammateMailbox.ts`。
- [Claude Code plan 与 stop](https://github.com/yasasbanukaofficial/claude-code)：`tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`、`tasks/stopTask.ts`、`coordinator/coordinatorMode.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/acp/acp/README.md`、`packages/subagent/subagent-acp/README.md`、`docs/subsystems/session.md`、
  `docs/subsystems/plan.md`、`docs/subsystems/approval.md`。
- [learn-claude-code · s16_team_protocols](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter10.md`（多 Agent 协作），以中文原版为准。
  先收尾再回 ack 的停止、砍掉那一层备用方案，以及第一个做成功就把整批停掉、靠一把锁让这件事只结算一次。
  两者都出自书作者自己的实验，属于单一来源。
- [A2A protocol](https://github.com/a2aproject/A2A)（Linux Foundation）：Agent Card discovery、task 生命周期的各个状态
  （`submitted`、`working`、`input-required`、`completed`、`failed`），以及跨信任边界的 opaque artifact 交换。
