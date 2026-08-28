# 0 · Harness thesis

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 模型负责判断，harness 负责让判断安全地变成行动。

模型负责推理、选择工具，以及判断何时停止。harness（外层架构）则是包在模型外面的代码，包括 loop、tool、memory、permission 和各种 interface。

单次模型调用只会根据输入产生一个响应。模型可以判断接下来该做什么，却无法自己执行。它没有持久状态、工具运行环境、文件存取能力，也没有权限关卡。

harness 必须：

1. 提供实际执行动作的环境。
2. 把有用的执行结果送回模型。
3. 在动作影响真实系统前先检查风险。
4. 保存状态，让后续调用能接续先前的进度。

没有 harness，模型就只能回答问题，无法执行工具、读取结果，也无法在多次调用之间保留工作进度。

---

## 核心机制

![机制图](assets/00-harness-thesis.png)

本章先厘清模型与 harness 的分工。模型调用位在核心，输入由 harness 准备，输出也由 harness 接手处理。

简单来说，模型负责判断，harness 负责环境与执行。

第 1 章的 loop 是核心控制流程。其他章节在它周围加上输入、检查或状态：

- 第 2 章加上 tool runtime 与 dispatch。
- 第 3 章加上 permission 与 sandbox。
- 第 4 章加上拦截生命周期事件的 hook。
- 第 8 章与第 9 章加上 context 管理与跨 session memory。
- 第 10 章在每一轮生成 system prompt。
- 后面的章节加上 task、background execution、scheduling 与 isolation。

这些部分不会替换 loop。它们把输入送进 loop、为 loop 把关，或替 loop 保存状态。

### Harness 不是越复杂越好

每一层 harness，都是在补当下模型做不到的事。这让每一层都带着两个成本：

1. 代码变多，要维护的变多，会出 bug 的地方也变多。
2. 设计绑着某一代模型。新模型可能自己就会规划、恢复、验证，这时还硬套旧的补救方式，反而会拉低表现。

所以 harness engineering 不是只有加，也包含删。模型换代时，重新评估每一层：还有帮助的留下，新模型自己就做得到的就删掉。
怎么量测，见第 20、21 章。mini-swe-agent 就是最极端的例子：几乎没有 harness，也就几乎没有东西需要重新评估。

deepseek-harness 是从另一端回答同一个问题。它每个部分都是 plugin，连 loop 也是，没有哪一块算是特权核心。
每种能力都有自己的扩展点，并由对应的 plugin 负责，例如工具、权限和 context 处理各自独立。
所以重新评估某一层只是改配置，不用 fork。要删掉一层，就是不要加载那个 plugin。

### 延伸阅读

以下两个框架出自 ai-agent-book，是可以拿去验的说法，不是本项目的结论。

**界线怎么验：**模型和 harness 的界线落在哪，会随着模型变强而移动。书上有两个说法可以拿去验：

- **scaffold 要多厚，看模型有多强：**弱一点的模型需要被逼着先规划，需要重试阶梯，需要写死的检查。强一点的模型自己就会做这些事。
- **什么时候该动手，是模型的策略：**不用再读、可以动手改的那个点，是模型训练时学来的。prompt 里写一句话、把步数压小，都只能推它一点，订不了它。

**这两个说法可以合成同一个测试：**同一套 scaffold 拿到两代模型上跑，分数可能往相反方向走。
这件事只有书上一次实验撑着，所以去验方向，不要直接信幅度。
接着逐层检查：这项能力应该由模型还是 harness 负责？如果模型已经能可靠完成，这一层就是多余的，只是白白浪费 token。

**Agent 先在哪里跑得起来：**一个任务现在适不适合交给 agent，看两件事：目标能讲多精确，还有结果机器判不判得出来。
写程序这两项都很高。一张 ticket 或一个失败的测试就把目标讲清楚了，测试、类型、linter 和 git 会说什么时候算做完。
这些基础建设本来是人盖给自己用的，agent 直接拿来当现成的验证 harness。coding agent 最早成熟，原因就在这里。

**少掉其中一项的话：**任务不是变得笼统地难，而是会用特定的方式坏掉，有两种：

- **目标清楚，但机器验不了：**例如把一页文字改得更好读。loop 没有停止条件，没人说不行，它就当作做完了。
- **机器验得了，但目标讲不清楚：**例如把一个模块整理干净。loop 会对着检查做，它能证明没有东西坏掉，但这不是你要的东西。

**这两种要用不同的方式补：**领域里本来就没有检查，第 21 章教你把检查建起来。目标讲不清楚的话，补再多检查也没用。

---

## 不同系统怎么做

哪些事让模型决定，哪些事交给周围的代码。

| | Claude Code | mini-swe-agent |
| --- | --- | --- |
| **优点** | harness 带来安全性、持久化、subagent，以及按需加载的知识。 | 几乎没有 harness 代码，也就没什么要维护的。 |
| **限制** | 代码大多集中在 harness，要维护的东西多，bug 也大多出在这里。 | 除了执行 bash 之外的每一种能力，都得靠模型自己。 |
| **设计原因** | 模型调用无法自行行动，所以环境全由 harness 负责。 | 假设一个 bash 工具就够了。hook、skill、memory 与 task 都刻意不存在。 |
| **做法：model owns** | 判断、选择工具、决定停止。模型看得到工具名称、schema 与结果。 | 判断、怎么改文件、何时提交。 |
| **做法：harness owns** | loop、tool、permission、hook、knowledge、task 与 coordination。 | 一个 loop、一个 bash tool、跑指令前先问过用户，再加上步数与成本上限。 |
| **做法：size signal** | 多数代码都落在模型调用之外。 | 整个 agent class 大约 150 行。 |

---

## 常见问题

- **把 harness 的行为归功给模型：**权限检查与错误复原是 harness 的行为。它们出错时要修的是 harness。
- **把该由模型做的决定写死：**僵硬的工具顺序与写死的规划会和模型冲突。需要判断时，就让模型去决定。
- **harness 太少：**一个没有工具、权限或 context 管理的 loop，会把模型停在聊天机器人的层次。补上缺少的那一层。
- **harness 太多：**每加一层就多一份要维护的代码，而且为旧模型设计的那一层，可能反过来拖住新模型。模型换代时重新评估，没有帮助的就删掉。
- **把模型的策略当成 harness 的配置：**什么时候停止搜集信息，是模型学来的，不是配置出来的。prompt 规则只能推它一点。这一层要不要留，量过再决定。
- **在机器判不出结果的地方跑 agent：**loop 分不出这是做完了还是做错了。补一个检查器，或是让人留在流程里。
- **职责混在一起：**把权限逻辑写进工具执行流程，会更难测试，也更难替换。应该维持清晰的契约，例如 `Tool.ts` 与 `PreToolUse`。

---

## 参考资料

- [Claude Code source (`cc-src/src`)](https://github.com/yasasbanukaofficial/claude-code)：`QueryEngine.ts`、`query/`、`Tool.ts`、`tools/`、`hooks/`、`types/permissions.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py`、`environments/local.py`、`__init__.py` 里的 protocol。
- [mini-swe-agent README](https://github.com/swe-agent/mini-swe-agent)：模型变强之后，harness 可以更小的理由。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/architecture.md`、`docs/capability-seams.md`、`docs/cordis-primer.md`、`docs/subsystems/core.md`。
- [learn-claude-code · s20_comprehensive](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter5.md`，以中文原版为准。界线框架与任务象限，两者都只有单一来源。
