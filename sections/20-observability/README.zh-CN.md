# 20 · Observability & evaluation

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 没有完整记录，就无法重现问题、控制成本，也无法可靠评估 agent。

agent 会在无人看管的情况下持续执行、产生副作用，并消耗成本。单看一次模型调用，就像面对黑盒子：token 花掉了，真实动作也发生了，却不知道中间经过什么。

没有 instrumentation，连最基本的问题都无法回答：它做了什么？某个工具失败几次？这个 session 花了多少钱？

本章专注在记录执行过程，把每一步做了什么、花了多少成本整理成可保存、可查询的数据。

至于某次修改究竟让质量变好还是变差，会在第 23 章处理；评估所需的素材，正是本章收集的记录。

如果没有这些记录，成本突然上升时找不到原因，bug 无法重现，eval dataset 也缺少真实案例可用。

---

## 核心机制

![机制图](assets/20-observability.png)

这里有两条彼此独立的 pipeline，而且都不改变 loop 的控制流程。

telemetry 会在 loop 的每一步调用 logger，送出事件后不等待结果，也就是 fire and forget。

event 的接收端称为 sink，可以是终端机、文件，或 Datadog 这类 backend。event 会先进入队列，等 sink 接上后，再经过采样与敏感字段清理，最后送往各个 sink。

evaluation 离线跑，用的是它自己的 task 集（第 23 章）。那组 task 就是拿本章记下来的东西做出来的。

- `emit` 永不阻塞、永不抛异常，所以一次 logging 故障无法卡住或弄垮 loop（第 1 章）。
- event 会先在队列里缓冲，等某个 sink 接上再一次送出，所以 loop 在 telemetry 就绪之前就能 log。
- 采样依速率丢弃 event；scrub 只保留白名单字段，所以代码与路径永不外泄。
- 成本按模型累加成一个 USD 总额，实时显示并在退出时显示。

### 本章添加：fire-and-forget 事件记录

`telemetry.py` 发出 event。event 先排在队列里，等某个 sink 接上，再采样、scrub，送给每一个 sink。`emit` 永不抛异常：

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

- 在任何 sink 接上之前，event 会在 `_queue` 里缓冲；`attach` 通过同一条 `_deliver` 路径把它们全部送出去，所以排队的 event 同样会被采样与 scrub。
- `scrub` 只保留 `SAFE_FIELDS`，所以一个未知安全的值（代码、文件路径、prompt）永远不会抵达 backend。
- 一个抛异常的 sink 会被吞掉，所以一个坏掉的 backend 无法卡住或弄垮 loop。

### 本章添加：每个模型的成本与离线 eval

成本按模型累加成一个滚动的 USD 总额：

```python
def add(self, model, input_tokens, output_tokens):    # src/telemetry.py
    i, o = self.by_model.get(model, (0, 0))
    self.by_model[model] = (i + input_tokens, o + output_tokens)
    pi, po = PRICES.get(model, (0.0, 0.0))             # modelCost.ts pricing tiers
    self.cost_usd += input_tokens * pi + output_tokens * po
    return self.cost_usd
```

- `add` 查出每 token 的定价，并把花费滚进 `cost_usd`，也就是实时与退出时显示的那个数字。
- 这个总额算的是整个 session。它说不出钱是花在哪个任务上。

这里的 `run_eval` 是最小规模的 eval。它把一组固定的 task 集重播到候选 build 上，数过了几题，返回一个比率。
第 23 章在同一个入口下面补上环境、模拟用户和重复执行，也讲清楚为什么比率小幅下滑通常只是噪声。

### 如何集成到现有架构

demo 把 telemetry 挂在 model wrapper 上。loop 不变：

```python
def model(messages, registry, system):
    r = client.messages.create(...)
    cost.add(MODEL, r.usage.input_tokens, r.usage.output_tokens)   # cost rollup
    tel.emit("model_call", model=MODEL, tokens=..., cost_usd=...)  # scrubbed event
    return r
run_turn([...goal...], lambda m, r, s: model(m, r, SYSTEM), reg, Session(mode=DEFAULT))   # the one agent call
```

- telemetry 从外部观察：wrapper 发出一个 event 并追踪成本，所以 `run_turn` 与 dispatch 与第 13 章逐位元组相同。
- sink 印出每个 event；session 成本在最后印出；接着一个离线 `run_eval` 为一组固定的 task 集评分。
- 上游的一切都不变。observability 是一个旁观者，不是 loop 里的一个新步骤。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book 和两套 tracing 标准，也未经下面表格的系统证实。

**用 span，不是扁平事件：**一个 span 是一次 run 里的一件工作：一次模型调用、一次工具调用、一次检索。一条 trace 就是整趟 run。
每个 span 会记下这些：

- 什么时候开始、跑了多久
- 成功还是失败
- 它的 parent 是哪一个 span
- 描述这件工作的 attribute，内容可以自己填

真正关键的是 parent 这一项。有了它，一次 run 的 span 就串成一棵树，从树顶往下读，你看得到哪一步失败、哪一步慢、每一根分支各花了多少钱。

扁平事件做不到这件事。一个 event 只说得出「有这么一次调用」，说不出这次调用属于哪一步。
一个用户请求可能变成很多次模型调用、工具调用和检索，有些巢状在别人里面，有些同时在跑。
事后只靠时间戳把它们理清楚，其实是在猜。

span 长什么样子，由两套标准讲定，你不用去猜 backend 想吃什么：

- OpenTelemetry 定义 span 本身：trace id、parent id、时间、状态、attribute。
- OpenInference 在上面补 LLM 这一块的名称：prompt、completion、model、token 数、工具调用。

照这套名称把点埋一次就好，之后换 backend 只是改配置，不用重写。

汇出跟 `emit` 守同一条规矩：留在热路径之外。span 先进队列，由后台 worker 分批送出，这样 collector 再慢，run 也一点都不受影响。
本章的 `emit` 就是它的扁平版；在同一批 event 上补一个 trace id 和一个 parent id，树就出来了。

**非线性的成本与每任务上限：**成本算的是模型读进去多少 token，而每一轮都要把整段对话重送一次。
所以第二轮返回的工具结果，第三、第四、第五轮还要再付一次钱。
context 里多出来的任何东西，后面每一轮都得再付一遍，总额爬升的速度比轮数快。光看步数，你算不出这笔帐。

有两个 harness 功能各砍掉帐单的一块，但两边省下来的不能相加：

- prompt caching（第 10 章）把没变过的那段前缀打折。
- compaction（第 8 章）把比较旧的对话从 context 里拿掉。

它们会重叠：compaction 拿掉的，正好是 caching 本来就会打折的那些 token。

一个 session 总额把这些全盖住了，因为它说不出钱是花在哪个任务上。
所以成本要算到任务层级，而且每个任务都给一个上限。上限拦下一次 run，跟步数上限拦下停不下来的 loop 是同一招（第 1 章）。
这件事只有书讲，书里也没有引外部来源，所以这套成本模型就当作者自己的现场经验看。

**trace 回流成 eval 集：**两条 pipeline 只在一个方向上交会：正式环境的一条 trace 变成一个 eval task。中间有三个步骤。

- **挑：**留下值得学的那几趟：报错的、用户重试或出手纠正的、花费远高于其他的。一次顺顺利利的 run 给不了新东西。
- **脱敏：**挡住代码和路径不进 backend 的那份白名单，同样挡住它们进 task 文件。
- **重建：**一条 trace 里有起始状态和每一次工具调用，所以它给得出这个 task 的起点，也给得出这次 run 本来该走到的结果。

这件事持续做，eval 集才跟得上用户真正在做的事。落到那里的东西，由第 23 章来评分。

---

## 不同系统怎么做

每个 agent 如何发出 telemetry、追踪花费，以及怎么喂养 eval 集。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | 低成本又安全地换来丰富的正式环境可见度。 | crash 掉的 run 也留得下文件。 | 不用另外埋点：模型看得到的，log 里就有。 |
| **限制** | 只说发生了什么，答案好不好看不出来。 | 正式环境 telemetry 几乎没有。 | 没附任何脱敏规则。送出去可能会漏，也可能重复。 |
| **设计原因** | 正式环境得盯住崩溃和成本，又不能碰 loop。 | 质量靠离线 benchmark 评分，完整记录最重要。 | session log 本来就是记录，直接把它送出去就好。 |
| **做法：telemetry** | event 先排队，等 sink 接上再采样、scrub。 | 每趟 run 一个轨迹档，每一步都存。 | 每一则 session 事件都经过脱敏那一关再镜射出去。 |
| **做法：cost tracking** | 每模型 token 按定价滚成一个 session 总额。 | 逐次计价，汇总成 run 与全局总额。 | 重放整份 log 算出 token 数，从不换算成钱。 |
| **做法：eval feed** | 源代码中没有；trace 脱敏后变成 regression 案例。 | 存下来的轨迹喂给 benchmark runner。 | 录下来的 run 不用密钥就能重放，当成固定样本。 |

---

## 常见问题

- **telemetry 落在热路径上：**一个会阻塞或抛异常的 logging 调用会卡住 loop（第 1 章）。一个要等网络响应的 span exporter 也一样。
  缓解：调用完不等结果，搭配 pre-sink 队列、每 sink killswitch，以及后台 worker 分批汇出。
- **敏感数据泄漏到 log：**代码、文件路径或 prompt 跑进一个一般存取的 backend，或跑进由 trace 生出来的 task 文件。
  缓解：白名单可记录字段，送出或存档之前 scrub 掉其余。
- **扁平事件流没有 parent 链接：**没人说得出哪次模型调用属于哪一步，一次失败的 run 只能靠时间戳慢慢拼。
  缓解：每个 event 都带 trace id 和 parent span id，用 backend 本来就认得的命名惯例。
- **成本漂移没被察觉：**一次模型替换或失控 loop 会让花费倍增，而一个 session 总额会盖掉那个真正在烧钱的任务。
  缓解：每模型与每任务的总额都实时和退出时显示，加上每任务的上限，还有 loop 的步数上限（第 1 章）。
- **eval 集跟正式环境脱节：**离线 task 漏掉了真实用法，于是软件包通过而用户失败（第 23 章）。
  缓解：持续把失败和昂贵的 run 脱敏之后筛进 task 集。

---

## 动手跑跑看

[`src/`](src/) 承接第 19 章并加上：

- [`telemetry.py`](src/telemetry.py)：event logger（`Telemetry.emit`、排队与送出、`sample`、`scrub`）、每模型的 `CostTracker`，以及离线的 `run_eval`。
- [`test.py`](src/test.py)：先排队再送出、采样、scrub 加上真实工具 dispatch 上的 sink 隔离、每模型成本，以及一个抓到退步 build 的 eval。
- [`demo.py`](src/demo.py)：一轮 agent 由挂在 model wrapper 上的 telemetry 观察、一个实时 session 成本，接着一个离线 eval。

loop 与 dispatch 都不变。telemetry 从外部观察，而被它喂养的 eval 在热路径之外跑（第 23 章）。

```bash
python sections/20-observability/src/test.py         # offline checks, no key
uv run python sections/20-observability/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code analytics](https://github.com/yasasbanukaofficial/claude-code)：
  `services/analytics/index.ts`（queue + `logEvent`）、`sink.ts`、`datadog.ts`、`firstPartyEventLogger.ts`、`sinkKillswitch.ts`、`shouldSampleEvent`。
- [Claude Code cost and diagnostics](https://github.com/yasasbanukaofficial/claude-code)：
  `cost-tracker.ts`、`utils/modelCost.ts`、`costHook.ts`（`formatTotalCost`）、`diagnosticTracking.ts`、`upstreamproxy/relay.ts`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/telemetry.md`、`docs/subsystems/token-meter.md`、`packages/llm/llm-replay/README.md`、`docs/subsystems/invariants.md`。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter6.md`，以中文原书为准。
  span 树、非线性的 agent 成本与每任务上限，还有正式环境 trace 回流成 eval 集。
  书里的成本分析没有引用外部来源，属于单一来源。
- [OpenTelemetry tracing](https://opentelemetry.io/docs/specs/otel/trace/api/)：span 本身、parent 链接、时间、状态与 attribute。
- [OpenInference](https://github.com/Arize-ai/openinference)：在 span 上替 LLM 与工具 attribute 命名的语义惯例。
- evaluation 不在 Claude Code 这份源代码里，在这个 repo 由第 23 章负责。保留的 task 集与 LLM-as-judge 仍以重建与一般做法描述。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：
  `agents/default.py` 的 `serialize` 和 `save`、`models/__init__.py` 的 `GLOBAL_MODEL_STATS`、`run/benchmarks/swebench.py`、`run/utilities/inspector.py`。
- 章节定位：[learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code)。
