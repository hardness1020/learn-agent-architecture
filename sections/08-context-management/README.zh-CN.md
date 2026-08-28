# 8 · Context management

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 控制 context 的大小，让长时间 session 仍能稳定运作。

`messages[]` 会随执行时间持续成长。每个 tool 结果、assistant 回复和 user turn 都会增加内容，长时间 session 最后一定会逼近模型的 context limit。

context management 会在下一次 model call 前整理旧内容，视情况删除、换成 stub、保存到外部，或浓缩成摘要，让 session 能继续使用。

当 context 被填满时：

1. API 可能直接拒绝请求。
2. 每次调用都会变慢、变贵。
3. 过时或低价值的内容会干扰当前任务需要的信息。

第 3 种情况称为 context rot。无关内容愈堆愈多，模型就愈难找到真正重要的信息。早在 context window 塞满之前，这件事就已经开始，agent 看起来仍能运作，但判断质量会逐渐下降。

因此，压缩不只是为了省空间和成本。in-context learning 很大一部分是在检索信息：单一、明确的事实容易找到，散落在几十轮对话中的线索则很难重新拼起来。
与其让模型每次都从头推导，不如先整理并保留结论。摘要做得好，即使 context window 还没满，也能提升回答质量。

没有这一层，长任务终究会因 prompt 过大或信息过于混乱而失败。

---

## 核心机制

![机制图](assets/08-context-management.png)

在摘要之前先用低成本的 reducer。低成本的 reducer 是在地处理，而且大致上不损失信息。摘要则要付出一次 model call，而且可能遗失细节。

Claude Code 采用分层的顺序：

```text
budget   -> 把巨大的 tool 结果存到磁盘，留下一段预览
snip     -> 丢掉中段的旧轮次，保留开头和最近的结尾
micro    -> 把旧的 tool 结果本体换成一个 stub
collapse -> 可选的独立 context 系统
auto     -> 用 LLM 把整段历史摘要成一则消息
--- 以上都做了还是 prompt_too_long 时 ---
reactive -> 截掉开头并重新摘要，有重试上限
```

顺序很重要。举例来说，大型的 tool 结果应该先被持久化，之后任何 pass 才可以用 stub 替换它的本体。

### 本章添加：缩减 pass

```python
def manage(messages, summarizer=None):                 # src/context.py, run every turn
    _budget(messages)                                  # persist huge results   (lossless)
    _micro(messages, KEEP_RECENT)                      # stub old result bodies (cheap)
    if summarizer and estimate_tokens(messages) > TOKEN_LIMIT:
        return _auto(messages, KEEP_RECENT, summarizer)  # summarize history (lossy, last resort)
    return messages
```

- `manage` 在每个 turn 执行低成本的 pass。
- `_budget` 把过大的 tool 结果写到磁盘，并留下一段简短的 preview。
- `_micro` 把旧的 tool 结果本体换成 stub。
- `_auto` 保留第一个 turn 和最近的尾端，然后摘要中间的部分。
- `summarizer=None` 在 demo 中停用了会损失信息的摘要。

### 如何集成到现有架构

context management 在每次 model call 之前执行：

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)   # 8 · keep context under the window
    response = model(messages, registry)
    ...
```

本章动到的是 loop 本体。前几章加的都是 tool 或 dispatch 行为，loop 本身不用改。但 context 缩减必须在每次 model call 之前跑，所以只能写进 loop 里。

loop 仍然维持同样的不变条件：它用一个有效的 `messages[]` 调用模型，接着附上响应和任何 tool 结果。

### 对照：把 tool 输出写出去

Claude Code 和本章的 `_budget` 都是就地把过大的 tool 结果缩小，被切掉的那段就永远没了。

deepseek-harness 从不动已经发生的事。session log 只会被附加，模型看到的 messages 只是这份 log 的一个投影。
每次缩减都是再写一则事件，说明要替换掉哪一段，所以 session 续跑或 fork 之后，重放出来的画面一模一样。

过大的 tool 输出走的是另一条路，而且更早。结果一超过内嵌上限，tool 一返回就直接送进 spill store。
store 把完整文字存起来，返回一个位址。留在 context 里的只有头尾预览、那个位址，以及一句提示：要完整内容就去读或 grep 这个文件。
所以输出还在：模型需要剩下的部分时，自己去把文件要回来。

[`src/spill.py`](src/spill.py) 就是这件事的精简版。它是对照用的 demo，没有接进 `manage()`，所以后面的章节照样沿用同一套 pass。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book，也未经下面表格的系统证实。

**stub 每次都要是同一串字：**用来替换 tool 结果的那段文字也算在前缀里，所以它每次都要一模一样。
第一次替换时就把它定下来，之后照抄，连 session 从磁盘还原回来也照抄。
stub 如果重新算过，带上新的时间戳或新的路径，前缀就变了，它后面的 cache 也没了。

**压缩和 cache 想要的刚好相反：**压缩要改写历史，cache 却是历史都不要动才划算。
历史只要动过，改动点之后的 cache 就全部失效，下一次调用得重读整段前缀。
每个 turn 都修一点，这笔帐就每个 turn 都要付。累积到 token 门槛再一次修完，只付一次。
不管走哪一种，压缩都跑在两次 API 调用之间，不能跑在一次调用里面。

**这个 pass 也可以交给服务器做：**Claude API 的 context editing 会把比较旧的 tool 结果从前缀里拿掉，harness 这边一行代码都不用写。
但它一样要重建一次 cache。所以它的位置在靠近 overflow 那一端，不是每个 turn 都用。

**摘要是写给当前任务的，不是写给整个 session：**把发生过的事情从头复述一遍，并不是下一次调用需要的东西。
换个问法：下一次调用还缺什么？照重要性由高到低，留这些：

- 已经定案的架构与设计决策。
- 添加或改过的文件，以及改了什么。
- 最近一次检查或测试的成败状态。
- 还没做完的 TODO，以及现在做到哪一步。

真要丢东西，先丢原始 tool 输出。大的结果 budget pass 早就写进磁盘了，真的需要再读回来就好。

---

## 不同系统怎么做

各 agent 如何决定要腾出空间，以及要移除什么。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | 长 session 能继续运行，缩减成本低，存下来的输出还能重读。 | 没有东西要调度、要调参，行为一眼就能看懂。 | 历史从不被销毁。 |
| **限制** | 各个 pass 要讲究顺序。摘要可能丢掉之后要用的细节。 | 历史只会成长。run 拖得比预算久，window 塞爆就中止。 | log 在磁盘上只会长大，还得管锁和 fold。 |
| **设计原因** | 互动式 session 没有固定终点，window 迟早会满。 | 假设预算会先让 run 结束（见第 21 章）。 | log 才是事实，所以要缩的是投影，不是历史。 |
| **做法：trigger** | token 门槛，外加 `prompt_too_long` 的后备。 | 每则 observation，在 render 时处理。 | 每一步都量一次压力，加上确认过的 overflow。 |
| **做法：strategy** | 先跑低成本 reducer（存档、清成 stub），最后才摘要。 | 过长的输出只保留头尾，没有压缩。 | 先写出去、再修剪，最后一则摘要事件。 |
| **做法：budget** | 保留 output 和安全缓冲空间。 | 每则 observation 上限一万字符。 | 依模型换算比例：0.8 触发压缩，保留 0.16。 |

---

## 常见问题

- **摘要漏掉需要的细节：**持久化完整输出，并在需要时重新读取文件。
- **压缩反覆失败：**使用 retry 上限或断路器。
- **单一巨大 turn 仍然 overflow：**对 `prompt_too_long` 做出反应，执行一次有界限的最后手段裁剪。
- **pass 顺序错误而遗失数据：**在把旧结果 stub 化之前，先持久化大型结果。
- **拆散的 tool 配对：**不要把一个 `tool_use` 和它相配的 `tool_result` 拆开。
- **stub 文字每次都不一样：**preview 若带着新的时间戳或路径重新产生，前缀就变了，cache 直接失效。stub 字符串第一次生成后就固定下来。
- **每个 turn 都修一点：**每次改动都会让改动点之后的 cache 失效，小修小补反而比一次修完贵。用门槛触发，成批处理。
- **模型全盘相信摘要：**注入的状态摘要，模型会当成事实读，几乎不会回头查证。摘要里留下指向原始文件的线索，写错了才追得回来。

---

## 动手跑跑看

[`src/`](src/) 沿用 07 并加上：

- [`context.py`](src/context.py)：`budget`、`micro` 和 `auto` 这几个 pass 都通过 `manage` 执行。
- [`loop.py`](src/loop.py)：在每个 turn 的最上方调用 `context.manage()`。
- [`spill.py`](src/spill.py)：deepseek-harness 的对照：过大的结果整份存起来，context 只留预览和文件路径。
- [`test.py`](src/test.py)：独立检查每一个 pass，再加上一次 spill，确认完整文字还读得回来。
- [`demo.py`](src/demo.py)：驱动已接上 context management 的 loop。

```bash
python sections/08-context-management/src/test.py         # offline checks, no key
uv run python sections/08-context-management/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：`services/compact/autoCompact.ts`、`microCompact.ts`、`timeBasedMCConfig.ts`、
  `compact.ts`、`utils/toolResultStorage.ts`、`query.ts`、`query/tokenBudget.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`config/mini.yaml` 的 observation template、`models/litellm_model.py` 的 `abort_exceptions`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/compaction/compaction/src/index.ts`、`packages/compaction/compaction-basic/README.md`、`packages/llm/token-meter/src/index.ts`、
  `packages/spill/spill/src/index.ts`、`packages/spill/spill-policy/README.md`、`docs/subsystems/compaction.md`、`docs/subsystems/session.md`。
- [learn-claude-code · s08_context_compact](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
- [ai-agent-book · 第 2 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md)（《深入理解 AI Agent》，李博杰，以中文原版为准）：
  context rot、in-context learning 其实是检索、压缩与 cache 的相互影响、针对任务的压缩与保留优先序、
  API 层的 context editing、冻结的 tool 结果 stub，以及模型会把注入的摘要当成事实这个结论。
- [Lost in the Middle](https://arxiv.org/abs/2307.03172)（Liu 等人，TACL 2024）：放在长上下文中段的事实，取用准确度会掉下来。这是 context rot 的依据。

以下是推测，在上面那份 Claude Code 源代码 repo 里找不到完整实现：

- `snipCompact.ts`：只看得到 `snipCompactIfNeeded(messages)` 的调用点。
- `reactiveCompact.ts`：reactive 路径看起来位于 `compact.ts` 中。
