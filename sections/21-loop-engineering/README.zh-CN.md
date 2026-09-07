# 21 · Loop engineering

[English](README.md) · [繁體中文](README.zh-TW.md) · **简体中文** · [日本語](README.ja.md) · [한국어](README.ko.md)

> 重点不再是下一句 prompt，而是如何设计一套能自行启动、验证与改进的 loop。

前面各章分别在 model call 周围加入一项机制，本章则把这些组件组合成完整系统。

Loop engineering 代表的是工程重心的转移。与其由人逐个 turn 下 prompt，不如建立外层系统，让它自己找出工作、启动 agent、检查输出并决定下一步。人的角色也从逐步操作，转为设计规则与边界。

外层 loop 必须：

1. 由 trigger 启动，不只依赖 user 输入（第 14 章）。
2. 输出通过检查后，任务才算完成。
3. 靠预先设定的 budget 停下来，而不是靠运气。
4. 保存状态，让下次执行能延续进度（第 9、12 章）。
5. 即使没有人实时监看，也要留下完整回报（第 20 章）。

少了这一层，人就得亲自充当外层 loop：下 prompt、读输出、判断结果并手动重试。人一离开，agent 也会跟着停止。

---

## 核心机制

![机制图](assets/21-loop-engineering.png)

最简单的理解方式，是在 agent loop 外再加上三层 loop。每一层负责回答不同问题。

1. **Agent loop**（第 1 章）：调用 tool 直到任务看起来完成。回答的是：这一步怎么做完。
2. **验证 loop（verification loop）：**拿 rubric（评分准则）替输出评分。没过就带着 feedback 重试，最多试到 budget 用完。回答的是：是不是真的完成了。
3. **事件 loop（event loop）：**cron 调度、webhook 和 channel 负责启动执行（第 14、19 章）。回答的是：工作什么时候开始。
4. **改进 loop（improvement loop）：**trace 和 eval（第 20 章）回头改 harness 配置、skill 或 model。回答的是：整个系统有没有变好。
   这个 loop 成熟到极致时，改的是 harness 本身：从 trace 里挖出弱点、提出一个范围受限的修改、再用 regression 测试验证。
   loop 的结构本身变成一个可以搜索的空间，而不是手工设计的模板。

数据由内往外流。trigger fire 之后把一个 prompt 放进 queue。agent loop 产出一个候选输出，评分者替它打分数。
没过而且 budget 还有剩，就带着 feedback 重试；过了就通过该 task 的 channel 投递出去。
这次执行做了什么，会记录成 trace 留在 telemetry（第 20 章）。改进 loop 之后就是读这些记录，来决定 harness 哪里该改。

### 本章添加：验证 loop

这是前面章节唯一没做过的 loop。内层 loop 是 model 自己说完成就停。有了验证 loop，「完成」不再是 model 说了算，要通过检查才算数：

```python
def verified_run(task, worker, checker, budget=2):    # src/verify.py
    feedback = ""
    attempts = []
    for n in range(1, budget + 1):                    # the ceiling: harness-enforced
        out = worker(task + feedback)                 # the inner loop (section 1)
        verdict = checker(task, out)                  # a separate checker (section 6)
        attempts.append({"attempt": n, "passed": verdict["passed"], "reason": verdict["reason"]})
        if verdict["passed"]:
            return {"ok": True, "output": out, "attempts": attempts}
        feedback = f"\n\nA prior attempt was rejected... Why it failed: {verdict['reason']}"
    return {"ok": False, "output": None, "attempts": attempts}   # budget spent: escalate
```

- 评分者是另一个 agent，用全新的 context（第 6 章）。让 worker 评自己的输出，多半都会给过。
  `agent_checker` 做的就是这件事：每次评分都在新的 `messages[]` 上跑内层 loop，verdict 的第一个字是 PASS 或 FAIL。
- rubric 定在 loop 之外。model 只能想办法满足它，不能改写它。
- feedback 是数据。没过的 verdict 会并进重试的 prompt，所以第二次尝试知道第一次错在哪。
- `ok: False` 是要交给人接手的信号。尝试记录会一并交出去；loop 不会永远重试下去。

只有一个过或不过，信号太薄。把判决拆成三个问题，每个问题都要自己说得出证据：

- **结果（outcome）：**这次执行有没有留下正确的状态。证据就是那个状态，能用程序检查的就用程序检查（第 23 章）。
- **过程（process）：**规则有没有守住：能用哪些 tool、顺序对不对、该确认的有没有跳过。证据是 trace 里的 tool 调用。
- **质量（quality）：**程序检查不出来的那部分，答案够不够好。证据是 rubric，而且要指名是哪一条没过。

可执行的 src 只评第三个问题。结果和过程要有一个会记录过程的环境才检查得了，那是第 23 章在做的事。
拆开的好处是知道该修哪里。结果过了、过程没过，代表这次只是运气好。过程过了、结果没过，代表规则本身写错了。

### Budget 与停止条件

每个 loop 都需要一个 model 说什么都绕不过去的上限：迭代次数、token budget、时间上限，或 dry counter（连续 K 轮都没有新发现就停）。

上限由 harness 强制执行。拜托 model 自己停下来只是提示，不是停止条件。
在 `verified_run` 里，上限就是 `range()` 的边界：第 `budget + 1` 次尝试不可能发生。

### 成熟度等级

Loop engineering 的几个出处都用「敢让它做多少事」来替 loop 分级：

- **L1 · 回报：**loop 只读取和回报。动手的是人。
- **L2 · 协作：**loop 起草修改。由人批准。
- **L3 · 无人看管：**loop 直接动手。人事后审计。

等级是一个权限决定（第 3 章）。只有在目前等级的输出已经稳定到让人觉得无聊时，才把 loop 升一级。

### 如何集成到现有架构

本章没有加任何新的基本组件。它是前面各章的组合：

- trigger 是第 14 章的 schedule 和第 19 章的 channel。
- worker 是第 1 章的 loop；maker 和 checker 的分工用第 6 章的 subagent。
- 并行的 loop 用第 15 章的 worktree 隔离。
- 执行之间的状态放在第 9 章的记忆和第 12 章的 task 记录。
- 回报和 trace 是第 20 章。改进 loop 把第 20 章量到的东西接回 harness 的修改。

可执行的 src 也是同一套组法。`run_turn` 完全没改，跟第 20 章一模一样；`verified_run` 只是在外面多包一层验证：

```python
def worker(prompt):                                # src/demo.py · the inner loop, unchanged
    return run_turn([{"role": "user", "content": prompt}], model, reg, Session(mode=DEFAULT))

checker = agent_checker(RUBRIC, model)             # a fresh grader agent, no tools
result = verified_run("What is 27 + 15? Use the add tool.", worker, checker, budget=2)
```

本章新加的是纪律：说完成之前先评分、开始之前先设 budget、无论如何都要回报。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book 和已发表的自我改进研究，也未经下面表格的系统证实。

**学到的东西该放哪：**假设某趟执行发现 staging 数据库要换一组连接字符串，这件事该存到哪？
改进 loop 难的不是找出教训，而是挑一个地方放。可以放的地方有四种：

- **知识文件：**某趟执行发现的一个事实。写进去便宜，删掉也便宜。任务需要的时候 agent 再读回来（第 9 章）。
- **Prompt 或 skill：**一种希望每次都重复的行为。代价是只要加载，每个 turn 都得付 context（第 7 章）。
- **程序：**一段每次都跑得一模一样的流程。推论的时候不花钱，而且测得起来（第 2 章）。
- **模型权重：**最后手段。慢、贵、最难反悔，也不在这个 repo 谈的 harness 范围里。

规则是挑装得下这个改动、而且最小的那一个。最小同时也代表最好验、最好收回。
连接字符串属于事实信息，应该写进文件，而不是放进 system prompt。

第二个去处，也就是 prompt 或 skill，最容易被滥用，所以要另外设关卡。
要改，就从发生过好几次的失败来改，不要只凭一次跑坏。
写清楚它什么时候才适用，不相干的执行才不会被它影响。接着验两次：一次用改动附近的案例，一次用写的时候没看过的 holdout set。
先上一部分流量，回退方案随时备着。
Karpathy 把这件事叫 system prompt learning：改的是文字，不是权重。
ACE 让每次改动都很小，只去改 context 里编了号的单项，不整段 prompt 重写。

**从用 tool 到做 tool：**第三个去处，也就是程序，是前面章节没做过的。
skill 交给 model 的是一份它还得自己读、自己照做的指示（第 7 章）；编译出来的 workflow 交给 harness 的则是一段不用 model 就能跑的程序。
假设 agent 已经订过十次同一类的票，把它变成程序有五步：

1. **捕捉（capture）：**把一次跑成功的执行记下来：调用了什么、顺序如何、每一步前后的状态长什么样。
2. **参数化（parameterize）：**每趟不一样的地方改成参数，一直没变的部分就是那段程序。
3. **在重置环境验证（validate on reset）：**在干净的环境重放一次（第 23 章）。每一步跑之前检查一次、跑完再检查一次，最后还要看整体状态对不对。
4. **重放（replay）：**下次遇到同类任务就直接跑这段程序。不用调用 model，所以又快又便宜，每次结果也都一样。
5. **失效（invalidate）：**只要有一项检查没过，这段程序就退场。任务交还给 model，由它去捕捉新的一段。

做 tool 走的是同一条路，只是从另一头开始：agent 遇到自己做不到的事，就去找现成的函数库，把它包成一个 tool，验证过了 registry 才收（第 2 章）。
两件事都是把一次昂贵的探索，换成一个便宜又检查得动的能力。
两件事也都少不了第五步，因为当初依赖的那个网站或 API 一定会变。

**改 harness 本身：**假设这个 loop 想改的不只是 prompt，而是 harness 的代码。那它要先有一份契约，才轮得到 patch。
这份变更契约要写四件事：哪些 trace 失败、失败得多频繁、根本原因是什么、这次改动预期改善什么，还有怎么收回来。
没有契约就不准动手。契约是给人看的，有没有它，决定了这是一个能自我修改的 loop，还是一个没人审得动的 loop。
它能改哪些代码要事先讲明。权限、budget 和关卡都放在那个范围外面，这个 loop 就碰不到（第 3 章）。

loop 能搜的范围是一道阶梯。最底下那阶是 prompt 里的一条规则。
往上按顺序是 context 怎么组装、workflow 有哪些步骤、harness 代码，最上面是那段负责提出修改的程序。
下面那阶失败了才往上爬一阶。每爬一阶，能搜的东西更多，能验的东西更少。
一条 prompt 规则，一天就能做完 A/B 测试；换掉提出修改的那段程序，之后每一个修改的产生方式都跟着变。

**在线执行，离线学习：**这两件事要分开。在线的 loop 只把任务跑完、把过程记下来。
它不做提炼、不升级 skill，也不改 prompt。
另外一个离线的 loop 才把很多趟执行一起读，找出反复出现的失败，写出候选改动，验证它们，再发布成一个版本。

分开之后，单独一次执行就改不动整个 agent。一条走运的路径不算规律。
某个网页叫 agent 记住的话，更不算证据。
要求好几趟执行都有同样的信号，再加上一道验证关卡，这两种东西就进不了正式发布。

拆开之后，该量的东西也变了。要看的是两个数字，不是一个：

- **更新（updating）：**这个 loop 产出的候选好不好。提了几个、几个通过验证、几个被收回去。
- **收益（benefit）：**发布出去的改动有没有用。该加载的执行有没有加载、加载之后 agent 有没有真的照做、holdout 上的表现有没有变好。

两个都要看。只看第一个，一个内容正确但从来没被加载的 skill，看起来就像一次失败的更新，loop 对自己的判断也就跟着错了。

---

## 不同系统怎么做

各个 agent 如何组合自己的外层 loop。

| | Claude Code | Hermes Agent | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- | --- |
| **优点** | verify 用程序编排，budget 是硬上限。 | 有 budget，改进也能回滚。 | 每趟 run 的帐单都有硬上限。 | 外层 loop 以 plugin 挂在公开的事件上。 |
| **限制** | 改进 loop 在源代码中没有闭环。 | 没有内置的评分重试 loop。 | 只做了 budget 这一半。 | 没有东西检查成果，只有轮数当预算。 |
| **设计原因** | 把外层 loop 当成一段可编排的程序。 | 目标是让改进闭合到 model。 | 一次 run 就是一个评分任务。 | loop 本身就是 plugin，控制自然挂在它上面。 |
| **做法：verification** | verify 阶段用程序编排：judge panel。 | maker 和 checker 分工，加离线测试。 | 没有，SWE-bench 离线评分。 | 没有内置，做完了没，由模型自己说。 |
| **做法：event loop** | Cron、自订节奏唤醒、remote trigger。 | cron 加受限 toolset。 | 没有，runner 排的是任务，不是时间。 | 提醒从 log 重放，以一个 turn 的形式进来。 |
| **做法：improvement loop** | workflow 可断点续跑，从 cache 重放。 | run 会变成训练数据。 | 没有，只有 budget。 | 没有现成的，但接的地方都留好了。 |

---

## 常见问题

- **没有停止条件（No stop condition）：**没有上限的重试 loop 会一直烧 token，直到有人看到帐单。缓解：由 harness 强制执行的迭代、token 和时间 budget。
- **自己评自己（Self-grading）：**worker 给自己的输出打分数，验证 loop 等于什么都没验。缓解：独立的 checker agent，加上定在 loop 之外的 rubric。
- **评什么都过（Rubber-stamp rubric）：**永远给过的评分者比没有还糟，因为它替烂输出盖上「已验证」的章。
  缓解：对抗式验证（要求 checker 想办法推翻），加上定期的人工抽查。
- **太早放手（Unattended too early）：**L1 的回报从来没人核对过，loop 就拿到了 L3 的写入权限。
  缓解：成熟度阶梯一次只升一级，由第 3 章的权限把关。
- **无声劣化（Silent drift）：**无人看管的 loop 越跑越差，却没有人读它的输出。缓解：heartbeat、一律投递的回报，以及第 20 章对通过率和成本的量测。
- **状态失忆（State amnesia）：**每次执行都重新发现同样的工作、重做一遍。缓解：把发现存进记忆或 task 记录（第 9、12 章），并在执行开始时读取。
- **自我修改的 harness 绕过关卡（Self-editing harness escapes its gates）：**能改 harness 代码的改进 loop，也能改那些把关它的代码。
  缓解：权限和 budget 放在这个 loop 改不到的地方（第 3 章）。
- **代理目标偏移（Proxy goal drift）：**开放式的工作里，rubric 只是真正目标的替身。loop 学会的是满足 rubric：
  挑熟悉的写法、把噪声当成发现、只留下有过的执行。分数一路往上，真正的目标却在偏。
  缓解：失败的执行也留在证据里、定期换新的 holdout set，并找人拿真正的目标来抽查输出。
- **每个教训都变成改 prompt（Every lesson becomes a prompt edit）：**prompt 最好写，所以什么都往里面塞，塞到自己的规则互相打架。
  缓解：看教训是什么再挑地方放。事实写进文件、流程写成程序，prompt 只留给非重复不可的行为。
- **编译好的 workflow 活得比环境久（A compiled workflow outlives its environment）：**网站或 API 已经改了，这段程序还是照跑，写错状态的速度比 model 还快。
  缓解：重放时每一步前后都检查，第一项检查没过就让这段程序退场。
- **在线执行自己升级自己的教训（The online run promotes its own lessons）：**执行途中就在提炼经验的 agent，可能把一条刚好走运的路径升上去，
  也可能把不可信网页故意留给它记住的文字升上去。缓解：在线的 loop 只记证据，其他都不做。发布前由独立的离线流程验证候选。

---

## 动手跑跑看

[`src/`](src/) 把 20 带了过来，并加上：

- [`verify.py`](src/verify.py)：验证 loop（`verified_run`：评分、带 feedback 重试、budget、交回给人）和 `agent_checker`，每个 verdict 都由一个全新的评分者做出。
- [`test.py`](src/test.py)：离线检查第一次就通过、feedback 有进到重试、budget 上限，以及 PASS/FAIL 的 verdict 约定。
- [`demo.py`](src/demo.py)：实际跑一次 verified run：worker 带着 add tool，独立的 checker 按固定 rubric 评分，budget 用完就交回给人。

loop 本身完全没改，验证那一层是包在外面的。

```bash
python sections/21-loop-engineering/src/test.py         # offline checks, no key
uv run python sections/21-loop-engineering/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `docs/subsystems/core.md`、`packages/workflow/tool-ralph/README.md`、`packages/schedule/schedule/README.md`、`docs/subsystems/goal.md`。
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering)：building block 与成熟度分级。
- [LangChain · The art of loop engineering](https://www.langchain.com/blog/the-art-of-loop-engineering)：四层堆叠的 loop。
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/)：building block 的组合方式。
- [MindStudio · What is loop engineering](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows)：目标条件。
- [Lilian Weng · Harness engineering for self-improvement](https://lilianweng.github.io/posts/2026-07-04-harness/)：深入谈改进 loop；关卡要放在 loop 之外。
- [ai-agent-book · 第 8 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter8.md)（《深入理解 AI Agent》，李博杰，以中文原版为准）：
  验证的三层、学到的东西该放哪与那条分流规则、改 prompt 的关卡、变更契约、meta 优化阶梯、
  在线与离线的拆分、进化指标的拆分，以及可验证闭环的边界。
- [PreAct](https://arxiv.org/abs/2606.17929)：把 trajectory 编译成带参数的 workflow，配上前置、后置与存档前检查，之后不用 model 就能重放。
  它的第一作者与本书作者同名，所以论文报的重放加速（大约 8.5 到 13 倍）当单一来源看待。
- [Alita](https://arxiv.org/abs/2505.20286)：能力缺口触发工具创建，验证过了才进能力库。
- Karpathy ·「system prompt learning」（X，2025 年 5 月 11 日）：改文字而不是改权重，被当成第三种学习范式。
- [ACE](https://arxiv.org/abs/2510.04618)：用稳定的 id 增量修改 context 单项，而不是整段 prompt 重写。
- [Lin et al.](https://arxiv.org/abs/2605.30621)：harness 更新和 harness 收益分开量，用换 model 的方式把两者分辨开来。
- [AHE](https://arxiv.org/abs/2604.25850) 与 [Self-Harness](https://arxiv.org/abs/2606.09498)：harness 自我修改时的变更契约与受限候选空间。
- [Claude Code](https://code.claude.com/docs)：`/loop`、`ScheduleWakeup`、`Workflow` schema。依据 tool schema 与文件记载的行为描述，非 source backup。
- [Hermes Agent 源代码](https://github.com/NousResearch/hermes-agent)：
  `agent/iteration_budget.py`、`cron/scheduler.py`、`tools/skill_manager_tool.py`、`hermes_cli/curator.py`、`agent/trajectory.py`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`agents/default.py` 的 `AgentConfig` 与 `query()`、`agents/interactive.py`、`run/benchmarks/swebench.py`。
