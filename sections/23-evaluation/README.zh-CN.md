# 23 · Evaluation

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md)

> pass rate 是否可信，取决于背后的测试环境是否真的量到你在意的行为。

第 20 章记录 production 中发生过什么，但光有记录仍无法判断质量。本章要回答另一个问题：这次修改是否真的让 agent 变得更好？

如果系统只有一次 model call，评估很单纯：送入 prompt，把输出和标准答案比较，再计算答对比例。

换成 agent 后，这种方法就不够了。agent 会执行多轮对话，主动向用户补问信息，也会通过 tool 修改环境中的数据。相同结果可能经由不同路径完成，而且即使 agent、harness、prompt 和 model 全都相同，同一个任务跑两次仍可能得到不同结果。

因此，agent evaluation 需要的不是一串 prompt，而是一个完整的测试环境：可 reset 的 state、模拟用户、控制对话流程的 protocol，以及检查最终环境状态的 rubric。

少了这些设计，仍然可以算出分数，但数字未必有意义。题目可能早已进入训练数据，新版本高出的 3 个百分点也可能只是抽样波动；甚至分数看似很好，agent 却在实际执行时退款了用户从未提过的订单。

---

## 核心机制

![机制图](assets/23-evaluation.png)

完整的评估环境包含五个部分，其中四个是数据，一个负责控制流程。

- **Dataset：**一笔一笔的任务记录。每一笔都写着起始 state、用户想要什么，以及这次要怎么检查。
- **Environment state：**任务会动到的那份数据，例如订单、文件、数据库。
  它要够真实，测起来才有意义；也要抓得住，随时能 reset 回原样。
- **Tools：**agent 可以执行的操作。要拆到够小（读一笔订单、退一笔订单），不要弄成一个叫「解决客诉」的 tool。
- **Rubric：**一次跑完以后，怎么换算成分数。
- **Interaction protocol：**谁什么时候说话，以及这一次什么时候结束。

第 20 章的评估接口是 `(input, grade)`：输入一个字符串、得到一个字符串，再计算 pass rate。本章保留同一个入口，但在底层补上完整的评估环境。

### 添加：环境和它的 reset

state 存在环境里，要改它只能通过环境给的 tool：

```python
def reset(self):                                       # src/evaluation.py
    self.state = deepcopy(self.initial)                # a fresh copy per episode
    self.calls = []

def call(self, name, **args):
    tool = self.tools.get(name)
    if tool is None:
        self.calls.append((name, False))               # illegal: no such tool
        return f"error: no tool named {name}"
    try:
        out = tool(self.state, **args)
    except Exception as e:                             # illegal: wrong arguments
        self.calls.append((name, False))
        return f"error: {type(e).__name__}: {e}"
    self.calls.append((name, True))
    return str(out)
```

- 每个 episode 开始前，`reset` 会把初始数据整份复制一份新的出来，agent 动到的是这份副本。
  所以下一次拿到的还是原本那份数据，上一次写过什么都看不到。
  少了这一步，退款任务跑第二次时，那笔订单已经是退过的状态。
- agent 叫了不存在的 tool，或参数给错，环境会回一句话讲清楚错在哪，而不是只回一个「失败」。
  看得懂错在哪，agent 才可能自己改对；改不改得回来，本来就是要测的能力。
- 每次调用都会记下这次合不合法。这份记录就是过程指标的来源：叫错几次、总共走了几步。就算最后结果检查过了，这些数字还在。

### 添加：模拟用户与 protocol

大部分 benchmark 第一则消息就把完整需求交给 agent。真实的用户不会这样讲话。
他们开头只会说「我的订单好像有问题」，剩下的你不问，他不会讲。

所以模拟用户手上有一份脚本，一轮只讲一件事。这样一来，agent 会不会主动问，才变成可以打分的能力：

```python
def run_episode(env, task, agent, max_turns=8):        # src/evaluation.py
    env.reset()
    user = task["user"]()                              # a fresh simulated user per episode
    transcript, said = [], user()
    for _ in range(max_turns):                         # the ceiling: an episode always terminates
        if said is None:
            break
        reply = agent(said, env)
        transcript.append((said, reply))
        said = user(reply)
    return {"transcript": transcript, "state": env.state, "calls": list(env.calls)}
```

可执行程序用的是固定脚本，离线检查才会每次都一样。实际跑的时候，是找一个 LLM 拿同一份脚本来演用户。
prompt 里会交代它：照角色回话、只讲这一步需要讲的、脚本里没有的不准自己编。每次讲法都不一样，但信息释出的顺序不会变。

再做完整一点，就是让模拟用户对同一份 state 也有自己的 tool。有些事只有用户做得到，agent 只能说服他动手，真实的客服电话本来就是这样。
这时候环境不再只有 agent 在改：用户也会动它，agent 得自己发现对方做了什么，不能假设现在的 state 都是自己造成的。

### 添加：一次 episode 怎么打分

三种检查，照顺序：

```python
def grade(task, run):                                  # src/evaluation.py
    checks = {name: bool(fn(run["state"])) for name, fn in task["checks"]}       # the outcome
    said = " ".join(reply for _, reply in run["transcript"]).lower()
    told = {s: s.lower() in said for s in task.get("must_say", [])}              # what was communicated
    unsafe = [name for name, fn in task.get("veto", []) if fn(run)]              # zero tolerance
    return {"passed": all(checks.values()) and all(told.values()) and not unsafe, ...}
```

- **看终态，不看路径：**检查读的是最后的 state，只要走到那个状态，中间怎么走都算过。标准解只是其中一种解法，不是规定要走的那一条。
- **该讲的话：**钱退了，却没告诉客人退了多少，这次不算做完。
  只看 state 的话，这种漏讲会算成通过；只看对话的话，agent 说「已经帮您退款」但其实没退，也会算成通过。两边都要查。
- **否决项：**只要踩到一次安全问题，这次就是不过，其他项目再漂亮也救不回来：退了客人没提过的订单、把机密印出来、把信寄给不相干的人。

### 指标：跑 k 次是为了什么

跑一次只能得到一个判定。同一个任务跑好几次，才问得出真正想知道的事：

- **Pass@k：**k 次里至少成功一次。回答的是「做不做得到」，探索型任务看这个。
- **Pass^k：**k 次全部成功。回答的是「稳不稳」，回归测试的门槛看这个。
- **Best@k：**k 次里最好的那次拿几分。用在开放式任务：分数是连续的，不是只有过跟不过。

这两个数字很快就会拉开。单次成功率 60% 的话，Pass@5 大约 99%，Pass^5 大约 8%。
指标挑错，明明只是运气好，看起来却像真的做出了什么。

另外还有一组过程指标，调用记录里本来就有：有效调用的比例、跟已知的好解法比起来多走了几步、重试几次、每个任务花多少钱。
它们回答的是另一件事：这次过关，是省着过的，还是硬撞过的。

### 开放式输出怎么评

有明确终态的任务，看 state 就够了。换成一段写出来的文字，没有东西可以直接比对，就交给另一个 model 照 rubric 打分。
打分准不准，几乎都看 rubric 怎么写：

1. **扎根于专家：**写进去的是这个领域的专家真正要检查的东西，不是文字通不通顺。
2. **涵盖要够全：**正确性、完整性、安全性都要顾到，常犯的错要直接写出来，不能让评判者自己意会。
3. **有权重、有否决：**标准分成必要、重要、可选，像编造事实这种否决项一出现，总分直接归零。
4. **每条都自己讲得清楚：**每一项都要能直接判断，不靠评判者自己的品味。
   「至少引用两份出处，并说明各自怎么支撑结论」可以判断，「展现了深刻的理解」不行。

评判者本身就是 model，model 有的毛病它都有：回答越长越容易拿高分，先读到的那一份也容易被偏袒。
评判者跟 agent 同一个家族的话，盲点也一样，agent 犯的错它刚好都会放过。

解法都不难：评判者换成不同家族的 model；配对比较时把顺序对调，再评一次；
大规模用之前，先拿人工标好的 gold set 校准；两边判不一样的，就送人工看。

### dataset 决定分数代表什么

环境做得再好，dataset 不行，跑出来的就是噪音。各家 benchmark 反复验证出四条原则。

- **可验证：**答案或终态不用人看就能判定。
- **分难度：**简单、中等、困难的任务要分开，这样「只在简单题上有用」的改动就藏不进平均分里。
- **人工看过：**要有人确认这题解得开、检查方式也公平。
  有些 benchmark 之所以会出一个专门的子集，就是因为原本的题目讲不清楚，或是用了不公平的测试在打分。
- **防污染：**公开的任务会流进下一轮训练数据。
  常见的做法有：每个任务文件里放 canary 字符串、答案不公开、收集模型 cutoff 之后才出现的任务，以及用参数化模板从同一个题型生出新题。

### 差距要怎么读

两份 build，100 个任务，70% 对 73%。这不算结果。

- **噪音带：**n 个任务上的成功率，标准误差大约是 `p(1-p)/n` 开根号。
  100 个任务、70% 的话大约 4.6 个百分点，所以 3 个百分点的差距整个埋在噪音里。
- **多跑几次：**采样本身有随机性，tool 响应的快慢也会影响结果，分数自然会浮动。每个配置跑三到五次，报波动范围，不要只报一个数字。
- **配对比较：**两份 build 跑的是同一批任务，所以逐题比，只看两边结果不一样的那几题。
  这样就把题目难易的影响扣掉了，需要的题数也比直接比较两个成功率来得少。
- **算一下你同时验了几个假设：**六个改动都用 95% 置信水平，其中至少一个纯靠运气看起来显著的概率大约是 26%。
  要么把门槛收紧，要么把跑赢的那个独立复跑一次，再决定信不信。

如果你预期的提升，比这套评估分辨得出来的差距还小，那接下来该做的是把任务集扩大，不是继续调 agent。

### 从报告到一次改动

benchmark 报告只拿来做一个决定：下一步要改什么。

1. **先怀疑评估本身：**process 被杀掉、grader 有 bug、任务跟正式环境早就对不上，这些在数字上跟 agent 变差长得一模一样。
   动 agent 之前，先把失败的 trajectory 读过一遍。
2. **找失败聚在哪：**总成功率 88%，但四个相关任务里挂了三个，这不是整体能力不足，是缺了某一项能力。
3. **一轮只改一个变量：**model、seed、任务集、步数上限都固定住，每一轮只动一件事。一轮改三件事，什么都解释不了。
4. **分清楚是谁的功劳：**harness 不动，只换 model，看 model 撑起多少；model 不动，关掉 harness 的某个组件，看那个组件值多少。
   这个 repo 的主张就是：这两个数字都存在。
5. **证据的规模要配得上决定：**四个任务可以支撑「值得跑大一点的实验」，不足以支持「可以上线」。

放进产品里，这些会变成常驻的基础设施。一个总开关可以关掉各种功能，量出裸 model 的 baseline。
Feature flag 负责分 AB 测试的组别，出事时也是断路开关。
每个 commit 都存一份完整展开后的 system prompt，改 prompt 就跟改代码一样，要跑一次评估。

做 AB 测试时，要把你直接动到的机制指标（计划长度、prompt 大小）和你真正在乎的目标指标（任务成功率、单次会话成本）分开。
另外留一组护栏指标：就算目标指标变好，护栏一破也要停下实验。

### 如何集成到现有架构

评估沿用既有的 harness，没有另外加东西：

- 环境的 tool 接口就是第 2 章的 registry，只是 handler 指向评估用的 state，不是真实世界。
- 受测的 agent 就是第 1 章的 loop，一行都没改。loop 里没有任何东西知道自己正在被评估。
- 评判者就是第 21 章的 checker：另一个 agent、全新的 context、一份它只能满足、不能改写的 rubric。
- 第 20 章负责供料：正式环境的 trace 脱敏之后变成新任务，它的 cost tracker 给出每个任务花多少钱。
- 改进 loop 就是第 21 章的外层 loop，只是这次带着证据：量一次、只改一件事、再量一次。

---

## 不同系统怎么做

各个系统怎么搭出分数背后的测试环境。

| | Claude Code | mini-swe-agent | τ²-bench | Verifiers |
| --- | --- | --- | --- | --- |
| **优点** | 烂结果在交出去之前就被挡掉。 | 跑 benchmark 的程序跟 agent 一起附在 repo 里。 | 看终态打分，走哪条路都算过。 | 环境、harness、model 分开配置，同一套任务集谁都能评。 |
| **限制** | 源代码里没有成套的评估。 | 只有 benchmark 的测试当 rubric。 | 用户由另一个 model 扮演，分数因此会飘。 | 跑一次性的评估也要先架好整套 runtime。 |
| **设计原因** | 在同一次执行里就把烂结果拦下来。 | 一个任务就是一个有测试的 repo bug。 | 客服工作本来就是一段对话。 | 评估和后训练要的是同一个环境。 |
| **做法：environment** | 重建：每次评估开一份用完就丢的工作区。 | 一个 instance 一个 container，换一个就是 reset。 | 一个领域数据库加一份政策文件。 | 每次执行都给一个全新的沙箱 runtime。 |
| **做法：task set** | 重建：正式环境的 trace 脱敏后留成固定题库。 | 公开 benchmark 的一个 split。 | 每个领域各自手写。 | 用 id 加载的模块，本机或在线都行。 |
| **做法：scoring** | 一个 reviewer agent 加一份固定 rubric。 | 该过的测试要过，本来会过的不能坏。 | 终态拿去跟标准解重放的结果比对。 | task 上的 reward 函数，加一个会跑代码的 judge。 |
| **做法：repeats** | 同一次执行里重跑验证。 | 一个 instance 跑一次。 | 同一题跑 k 次，看的是稳定度。 | 每题跑几次 rollout 是个参数。 |

---

## 常见问题

- **只看对话，不看实际结果（Grading the transcript）：**agent 说「已退款」，跟真的退了款拿到一样的分数。
  缓解：检查终态，另外再检查该讲的话有没有讲。
- **任务被污染（Contaminated tasks）：**公开 benchmark 会进到下一轮训练数据，高分可能只是背过。
  缓解：任务文件里放 canary 字符串、答案不公开、收集 cutoff 之后才出现的任务，以及用参数化模板生新题。
- **把噪音当结果（Reading noise as a result）：**100 个任务各跑一次，差 3 个百分点，什么都决定不了。
  缓解：多跑几次、逐题配对比较、差距落在噪音带里就当它不存在，同时验很多改动时把门槛收紧。
- **评估坏了却怪 agent（Blaming the agent）：**机器资源不够、grader 有 bug、任务过期，看起来都跟 agent 变差一模一样。
  缓解：改 agent 之前，先读失败的 trajectory。
- **评判者跟 agent 有同样的盲点（Shared blind spots）：**同家族的 judge 会放过 agent 常犯的错，偏好长回答，也偏好先读到的候选。
  缓解：换不同家族的 judge、顺序对调各评一次、先用人工标注的 gold set 校准。
- **钻分数的漏洞（Reward hacking）：**agent 找到拿分的捷径，跳过真正的工作：塞关键字、讨好 judge、遇到难题就回避。
  缓解：rubric 里放否决项、结果指标旁边摆过程指标，再定期人工抽检。
- **这套评估看不出改动（A suite that cannot see the change）：**40 个任务上 2 个百分点的提升根本量不出来，每一轮都只能写「看不出差别」。
  缓解：先把任务集扩大，再继续迭代。
- **上一次的 state 留到下一次（State leaking between runs）：**没有 reset，或只 reset 了浅的一层，上一个任务写下的东西就决定了下一题的分数。
  缓解：每个 episode 深拷贝一份，每次执行各用一个独立的环境（第 15 章）。

---

## 动手跑跑看

[`src/`](src/) 把 22 带了过来，并加上：

- [`evaluation.py`](src/evaluation.py)：带 `reset` 和调用记录的环境、一轮只释出一项信息的模拟用户、episode 的 protocol、
  打分（state 检查、该讲的话、否决项）、Pass@k 与 Pass^k、二项分布的噪音带，以及两份 build 的配对比较。
- [`test.py`](src/test.py)：离线检查 reset 有没有把 state 还原、protocol 跑一次时 agent 必须先问订单编号、
  结果检查明明有过却被否决项挡下、同一个不稳定的 build 上 Pass@k 与 Pass^k 的差别，
  以及退步的 build 分数更低、配对比较能指出它弄坏了哪几题。
- [`demo.py`](src/demo.py)：实际跑一次并打分。
  model 扮演客服 agent，它调用的 tool 都打在环境上，最后 harness 看它留下的 state 给分。

loop 本身完全没改。让分数有意义的，是它跑在什么环境里。

```bash
python sections/23-evaluation/src/test.py         # offline checks, no key
uv run python sections/23-evaluation/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [ai-agent-book · 第 6 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter6.md)（《深入理解 AI Agent》，李博杰，以中文原版为准）：
  评估环境的五个要素、渐进式信息披露、指标词典、rubric 四准则、统计显著性、
  从 benchmark 报告到系统改进，以及内部评估基础设施。
- [τ-bench](https://arxiv.org/abs/2406.12045)（Sierra）：用另一个语言模型扮演用户；
  成不成功，是拿对话结束时的数据库状态跟标注好的目标状态比对，另外还有衡量可靠度的 Pass^k。
- [τ²-bench](https://arxiv.org/abs/2506.07982) 和[它的源代码](https://github.com/sierra-research/tau2-bench)：双控环境，
  模拟用户自己也有 tool；以及 reward basis（数据库终态哈希后跟标准解重放的结果比对、必须讲到的字符串、
  可选的 LLM 判定条件），各项相乘才是总分。
- [Verifiers](https://github.com/willccbb/verifiers)：环境负责安排多个 agent 之间的流程、用 id 加载任务集、
  每题可以跑多次 rollout、续跑时只补跑漏掉或出错的那些，以及能对执行产物跑代码的 judge agent。
- [mini-swe-agent 源代码](https://github.com/swe-agent/mini-swe-agent)：`run/benchmarks/swebench.py`，
  一个 instance 一个 container image，每个 instance 各自留下 trajectory 与预测记录。
- [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)：500 题人工验证过的 instance，
  打分方式是原本失败的测试要变成通过，本来就会过的测试不能坏。
- [GAIA](https://arxiv.org/abs/2311.12983)：466 题，其中 300 题的答案不公开，排行榜就抓不走。
- [BIG-bench](https://github.com/google/BIG-bench)：每个任务文件都带 canary 字符串，避免题目被爬进训练数据。
- [Rubrics as Rewards](https://arxiv.org/abs/2507.17746)（Scale AI）：列表式的 rubric，写明要提到哪些事实、
  要有哪些推理步骤，以及哪些常见错误必须扣分。
- [Claude Code](https://code.claude.com/docs)：workflow 约定里的 reviewer 与 judge 阶段。
  内容依据 tool schema 和文件记载的行为，不是 source backup。评估软件包不在源代码里，那几格都标成重建。
