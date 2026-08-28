# 22 · Graph engineering

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 已知的流程交给代码控制，只有真正需要判断时才调用 model。

第 21 章把多层 loop 叠在 agent 外面，本章则进一步整理多次 model call 之间的流程。

许多任务的步骤在调用 model 前就已经很清楚，例如先分类工单再处理、先 review diff 再 commit，或先取得批准再执行外部动作。
一般 agent loop 每次都询问 model 下一步该做什么，等于重新探索一次既定流程。这种 routing 不只速度慢、消耗 token，执行结果也不够稳定。

Graph engineering 的做法，是把已知流程用代码写成一张有向图（directed graph）：

1. **Node** 负责执行工作，可以是一般代码、一次 model call，或完整的 agent run。
2. **Edge** 决定下一个 node，由 harness 用代码选择，不必再问 model。
3. **Cycle** 让流程可以回头，适合重试、review 后修改，或人工暂停后继续。
4. **State** 是沿着图传递的数据，每个 node 读取目前状态，再写回自己的更新。

原则很简单：已知流程写进代码，只有需要语义判断的部分才交给 model。第 21 章的 loop 就是最小型的图，由两个 node 和一条回边组成；本章会把它扩展成节点更多、连接方式更弹性的结构。

---

## 核心机制

![机制图](assets/22-graph-engineering.png)

最简单的版本只有三样东西。一个 dict 把 node 名称对到要跑的函数，另一个 dict 记着每个 node 跑完接谁。
再加一个 state dict，每个 node 都从里面读数据、把自己的改动写回去，一路带到结束。

```python
def run_graph(nodes, edges, state, start, budget=20):  # src/graph.py
    state = dict(state)
    trace = []
    node = start
    for _ in range(budget):                        # the ceiling: harness-enforced
        state.update(nodes[node](state) or {})     # a node returns only its updates
        trace.append(node)
        step = edges.get(node, END)
        node = step(state) if callable(step) else step   # a coded edge: no model call
        if node == END:
            return {"ok": True, "state": state, "trace": trace}
    return {"ok": False, "state": state, "trace": trace}   # budget spent: escalate
```

- `nodes` 是一张 dispatch map（第 2 章）。node 读 state，只返回自己改动的 key。
- edge 可以是固定的名字（决定性的），也可以是吃 state 的函数（条件式的）。两种都由 harness 用代码判断，routing 不花任何 token。
- 没有 edge 的 node 就是图的终点。budget 是第 21 章的上限：cycle 撞到上限就停，返回 `ok: False` 交给人。
- `trace` 按顺序记下跑过哪些 node，就是这次执行留给第 20 章的记录。

### Node：从纯代码到完整 agent

每个 node 都在纯代码和完整 agent 之间选一个位置：

- **Code node：**解析、验证、固定的 API 调用。决定性的，不花 token。
- **Model node：**一次 LLM 调用，例如分类器。有限度的判断。
- **Agent node：**一整个第 1 章的 loop，带着 tool。开放式的判断，但被固定在一个位置上。

`agent_node` 把内层 loop 包装成一个 node。每次经过时都会根据 state 生成 prompt，再用全新的 `messages[]` 执行 `run_turn`，
所以这个 node 只看得到 prompt builder 给它的部分，不是整趟执行。

怎么选？原则就是省 token：分支条件写得出来的，就交给代码；model 调用只留给真的需要判断的 node。

### 常见的图形

出处里叫得出名字的 workflow pattern，其实都是图形：

- **Prompt chaining：**一串 node 排成一条路，中间用代码把关。
- **Routing：**一条条件式 edge，分流到各个专门的 node。
- **Parallelization：**几条同时跑的分支在一个 node 会合。可以是拆工作（sectioning），也可以是同一件事跑多次投票（voting）。
- **Orchestrator-workers：**一个 node 在执行时决定要派出多少工作，再由一个 node 收拢。edge 是动态的，但形状仍然是图。
- **Evaluator-optimizer：**一个 worker node、一个 checker node，加一条往回的 edge。这就是第 21 章的验证 loop，放进图里变成一个子图。

各家的讲法还没统一。同样的东西，`ai-agent-book` 用的词是「collaboration topology」和「orchestration」，「graph engineering」它只在术语注记里提了一句。
本章还是用自己的名字，因为它讲的就是一张写在代码里的图。你去看别的来源时，对照的是机制，不是那个词。

### 什么时候不要画图

开放式的工作没办法预先定好流程。深度研究和难查的 bug 需要边跑边规划；事先画死的图，反而挡住解法需要走的那条路。
出处给的原则：只把你本来就要强制执行的结构写进图里（先分类再处理、先 review 再 commit、先批准再送出），
而且只在确实改善结果时才加结构。其他的都交给普通的 loop，让 model 自己规划。

最常见的其实是混合式：把 agent 当成固定图里的一个 node。图保证 review 一定会发生，agent 决定在自己的位置里怎么把事做完。

### 如何集成到现有架构

本章只加了一个小组件（edge map），其他都沿用前面的：

- node 做的事就是第 1 章的 loop；`agent_node` 原封不动包住 `run_turn`。
- 代码判断的 edge 沿用第 2 章的 dispatch 纪律：查表，不是 model 的输出。
- worker 和 checker 分属不同 node 是第 6 章；并行的分支用第 15 章的 worktree 隔离。
- step budget 和交回给人的约定是第 21 章。
- trace 交给第 20 章的 telemetry：看哪些 edge 有 fire，就知道哪些分支是死的。

可执行程序接的就是上面那张图：

```python
nodes = {                                          # src/demo.py
    "classify": lambda s: {"route": "math" if any(c.isdigit() for c in s["task"]) else "prose"},
    "math": agent_node(prompt, model, math_reg),   # a full agent run as one node
    "prose": agent_node(prompt, model, Registry()),
    "check": check_node,                           # section 21's checker, now a node
}
edges = {
    "classify": lambda s: s["route"],              # a coded edge: routing costs no tokens
    "math": "check",
    "prose": "check",
    "check": lambda s: END if s["verdict"]["passed"] else s["route"],   # the cycle
}
```

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book，也未经下面表格的系统证实。

**Phase node：**phase node 把一件工作拆成好几个阶段来跑，而每个阶段共享同一份 `messages[]`。
Explore、implement、review 是同一件工作的三个阶段，不是三件工作。
前一个阶段查到什么，trajectory 就带到下一个阶段，所以没有哪个阶段需要把任务从头再读一遍。

**每个 phase 的 tool：**每个 phase 有自己的 system prompt，也有自己的一套 tool，换 phase 的时候 harness 两样一起换掉。
history 原封不动留着，所以没有东西要打包给下一个 phase。书里写的三个 phase 是：

- **Explore：**读取和搜索。
- **Implement：**编辑和执行。
- **Review：**读取，再加一个返回结论的 tool。

**Gate tool：**model 想离开一个 phase，就调用一个 gate tool，例如 `finish_exploring`。
harness 把这个调用当成 edge，接着开始下一个 phase。gate 是唯一的出口，所以一个 phase 什么时候结束，是 harness 说了算，不是 model。

**路线：**先跑 explore，再跑 implement，最后 review。review 没过就把执行送回 implement，
implement 接着往下做，review 写的东西本来就在 trajectory 里。用本章的讲法，这就是一条路加一条往回的 edge，
跟前面的 evaluator-optimizer 同一个形状。

**要挂哪一种：**分支之间没关系，就用全新的 `messages[]`；几个 node 是同一件工作的不同阶段，就留同一条 trajectory。
全新的 `messages[]` 让每个 node 的 window 都很小，分支之间也互不干扰。
只保留一条 trajectory 的好处，是前面找到的信息仍然可见；代价是流程越长，占用的 context window 就越多。这属于 context 分配问题（第 8 章）。

**这样算不算 multi-agent：**书把这个做法算成 multi-agent，理由是每个 phase 的 prompt 和 tool 都换掉了。
这个 repo 则算成同一个 agent 换了 prompt 和 tool。用哪个名字，机制都是同一个，所以引用这个结果的时候，先讲清楚你用的是哪个定义。

**只有一个来源：**这个做法的依据是书里自己做的实验，没有第三方的报告佐证。

---

## 不同系统怎么做

各个 agent 怎么决定下一步跑什么。

| | Claude Code | Hermes Agent | mini-swe-agent |
| --- | --- | --- | --- |
| **优点** | Routing 是代码：不花 token、不会变来变去。续跑时跑完的 node 从记录重放。 | 不用事先画图，任务长什么样，结构就长什么样。 | 整张图一眼就能看完。 |
| **限制** | 图活在单次执行的 script 里，不是可以重用的声明式图。 | Routing 花 model 的 token，每次跑可能不一样。 | 所有任务共享同一个形状，没有分支可以特化。 |
| **设计原因** | 把编排当成程序：script 写好一次，harness 每次都决定性地执行。 | 假设助手型工作太开放，结构没办法预先声明。 | 一个 baseline：所有选择都留在 model 里，harness 只留一个 cycle。 |
| **做法：nodes** | 一个 node 一个 subagent，返回通过 schema 验证的结构化输出。 | 委派出去的 subagent，深度和并行数都有上限。 | 两个：一个 model step、一个 environment step。 |
| **做法：routing** | 阶段之间用普通的 script 代码：条件、循环、pipeline、并行分派。 | model 用 tool call 选路，没有写在代码里的 edge。 | 一个固定的 cycle，跑到 model 提交或 budget 用完为止。 |
| **做法：state** | 阶段的返回值往下传；journal 记下每个 node 的输出供续跑。 | 结果经过 completion queue 回到调用方。 | message list 就是全部的 state。 |

---

## 常见问题

- **Model 当 router（Model as router）：**把选路交给 model，烧 token、增加延迟，而且每次跑不一样。最上游选错一次，后面全部跟着错。
  缓解：转移用代码判断；model 调用留给需要判断的 node。
- **过度画图（Over-graphing）：**需要探索的任务被固定的图框住，解法要走的路被挡掉。
  缓解：只把本来就要强制执行的结构写进图里；开放式的工作留给普通的 loop。
- **没有失败的路（No failure edge）：**负责检查的 node 遇到 FAIL 却无路可送，烂输出就一路流到下游。
  缓解：每个检查 node 都给一条带 budget 的往回 edge（第 21 章）。
- **没有上限的 cycle（Unbounded cycle）：**没有上限的重试 edge 会永远绕下去。缓解：harness 强制执行的 step budget；budget 用完就交给人。
- **State 膨胀（State bloat）：**每个 node 都把完整输出倒进共享的 state，后面的 node 被淹没。
  缓解：严格的 state 边界；node 只读需要的子集，只返回自己的更新（第 8 章）。
- **跑到一半挂掉（Mid-run death）：**一张长图在第七个 node 挂掉，重来却从第一个 node 开始。
  缓解：记下每个 node 的输出；续跑时跑完的 node 从记录重放（第 11、12 章）。
- **Phase 走不完（Phase that never ends）：**model 一直不调用 gate tool，这个 phase 就用同一份 prompt、同一套 tool 一直做下去，只有 budget 停得了它。
  缓解：gate 是唯一的出口；每个 phase 各自有 step budget；budget 用完就往下一个 phase 走，或者交给人。
- **Trajectory 背着每个 phase（Trajectory that carries every phase）：**只有一条 trajectory，每过一个 phase 就长一截。里面还留着现在没挂的 tool 的调用记录，model 可能会再叫一次。
  缓解：在 phase 的 prompt 里写清楚现在是哪个 phase、有哪些 tool；叫到没挂的 tool 就回一个清楚的错误；跑完的 phase 拿去 compact（第 8 章）。

---

## 动手跑跑看

[`src/`](src/) 把 21 带了过来，并加上：

- [`graph.py`](src/graph.py)：`run_graph`（node 的 dispatch map、固定和条件式的 edge、一路传下去的 state、step budget）和 `agent_node`，把内层 loop 挂成一个 node。
- [`test.py`](src/test.py)：离线检查串接顺序和 state 合并、纯代码的 routing、cycle 撞到 budget 就停，以及 agent node 每次经过都用全新的 `messages[]`。
- [`demo.py`](src/demo.py)：照着图实际跑一次：code node 分类、代码 edge 选路、agent node 作答、第 21 章的 checker 评分，没过就带着 feedback 绕回去。

loop 本身完全没改。什么时候轮到它跑，由图决定。

```bash
python sections/22-graph-engineering/src/test.py         # offline checks, no key
uv run python sections/22-graph-engineering/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [LangChain · 3 years of graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph)：node、edge、cycle、把 agent 当 node，以及什么时候不要画图。
- [Anthropic · Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)：workflow 与 agent 的分界，加上五种 workflow 图形。
- [Google · Why we built ADK 2.0](https://developers.googleblog.com/en/why-we-built-adk-20/)：用代码选路、node 之间的 context 隔离、在 workflow 的 node 上挂 agent。
- [Claude Code](https://code.claude.com/docs)：`Workflow` script 的约定（pipeline、并行分派、结构化输出、续跑）。内容依据 tool schema 和文件记载的行为，不是 source backup。
- [Hermes Agent 源代码](https://github.com/NousResearch/hermes-agent)：`tools/delegate_tool.py`、`tools/async_delegation.py`、`batch_runner.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/workflow.md`、`packages/workflow/tool-workflow/README.md`：每次执行由模型现写脚本，不留下任何图。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py` 的 run loop 与 budget、`run/benchmarks/swebench.py`。
- [ai-agent-book · 第 10 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter10.md)（《深入理解 AI Agent》，李博杰，多 Agent 协作，以中文原版为准）：
  在同一条 trajectory 上做多阶段角色转换：每个 phase 一份 system prompt 和一套 tool，phase 之间用 tool call 当关卡，review 可以绕回实现。
  这个做法的证据只有书里自己做的实验。同一章主要用的词是「collaboration topology」和「orchestration」。
