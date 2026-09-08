<h1 align="center" style="margin-top: 0;">Learn Agent Architecture</h1>

<p align="center">
  <strong>学会现代 AI agent 如何围绕 LLM 打造。</strong><br>
</p>

<p align="center">
  <a href="#各章节"><img src="https://img.shields.io/badge/Focus-Harness_Engineering-8250df" alt="Focus: Harness Engineering"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-d29922" alt="License"></a>
  <br>
  <a href="https://github.com/anthropics/claude-code"><img src="https://img.shields.io/badge/Claude_Code-D97757" alt="Claude Code"></a>
  <a href="https://github.com/NousResearch/hermes-agent"><img src="https://img.shields.io/badge/Hermes_Agent-1A1A1A" alt="Hermes Agent"></a>
  <a href="https://github.com/swe-agent/mini-swe-agent"><img src="https://img.shields.io/badge/mini--swe--agent-7E56D8" alt="mini-swe-agent"></a>
  <a href="https://github.com/deepseek-ai/deepseek-harness"><img src="https://img.shields.io/badge/deepseek--harness-4D6BFE" alt="deepseek-harness"></a>
</p>

<p align="center">
  <img src="https://github.com/user-attachments/assets/472d8152-5e46-4e39-9f09-e77dcd07936a" alt="Learn Agent Architecture">
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-TW.md">繁體中文</a> · <strong>简体中文</strong> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a>
</p>

模型负责推理，harness（外层架构）则负责把推理变成可控的行动。工具怎么执行、状态怎么保留、副作用怎么限制，以及多个 loop 怎么协调，都不是一次模型调用能处理的事。

这个 repo 会逐章拆解 harness 的核心组件，包括 loop、tool、memory、permission、context、task 和 interface。
读完之后，你会更容易看懂各种 agent。coding 工具、聊天助手和自动化运行器看起来差很多，但核心差异通常都来自 harness 的设计选择。

如果想把单一主题学得更深，也可以接着看这两个延伸 repo：

- [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)：把 memory loop 扩展成适合 production 的完整 memory 子系统。
- [learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness)：从零重建 deepseek-harness，每一章专注拆解一个 plugin 接口。

**目录：** [Agent loop](#agent-loop) · [学习方法](#学习方法) · [研究的系统](#研究的系统) ·
[各章节](#各章节) · [文件结构](#文件结构) · [执行示例](#执行示例)

---

## Agent loop

![The agent loop](assets/the-agent-loop.png)

大多数 agent 都共享同一套控制流程：调用模型、执行它要求的工具、把结果加回对话，然后再次调用模型。

这个 loop 很小。大部分的工程都在它周围：分发工具、把关副作用、管理 context、保存状态，还有协调其他 loop。

---

## 学习方法

每一章都可以独立阅读，并从四个固定角度切入：

1. **问题：** 这一层为什么存在，又要解决什么。
2. **核心机制：** 通用的设计方式与控制流程。
3. **实际做法：** 真实系统如何实现同一个概念。
4. **常见问题：** 哪些地方容易出错，以及如何处理。

建议的学习方式：

- **照顺序读。每一章都建立在前一层之上。**
- 遇到可执行的章节，先读 `src/loop.py`，再跑它的 `demo.py`。
- 比较相邻章节的 `src/` diff，差异通常就是该章新加入的机制。

---

## 研究的系统

每个系统都是下面各章节的实现示例。

| 系统                     | 大家为什么用它                                                        | 值得看的地方                          | 覆盖章节                  | 研究版本  |
| ------------------------ | --------------------------------------------------------------------- | ------------------------------------- | ------------------------- | --------- |
| **Claude Code**    | 站在前沿的 coding agent：改文件、跑指令，直接在真实 repo 里完成改动。 | 完整 harness 架构，从这里读起         | 0 到 23（全部）           | v2.1.88   |
| **Hermes Agent**   | 长期助手：记得你、学会你的工作流程，还能跨平台跑任务。                | Memory、skills、always-on channels    | 7、9、14、16、19、21、22  | v2026.7.1 |
| **mini-swe-agent** | 研究基准：一个 bash 工具，约 150 行。                                 | 最小的完整 loop、budget、eval harness | 0 到 3、8、10、11、20 到 23 | v2.4.5    |
| **deepseek-harness** | Plugin 优先的 harness：连 loop 都是可替换的 plugin。 | Plugin 扩展点、durable session log、ACP | 1 到 8、10 到 14、16 到 21 | dsh-v0.1.0-rc.7 |
| *(更多陆续加入)*       |                                                                       |                                       |                           |           |

> 之后可以再加入更多系统，例如 OpenClaw 和 aider。
> 另外有两个延伸 repo：[learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) 专讲 memory 这一层，
> [learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness) 则是从零开始学 deepseek-harness。

---

## 各章节

八层，从最基本的 loop 一路到能自己运转的 harness。每一行都连到一篇可独立阅读的说明。

> [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) 会继续延伸第 9 章，用十个阶段把基础 memory loop 扩展到 production 规模。

![The learning path](assets/learning-path.png)

| #  | 章节                                                                        | 问题                                | 关键机制                                              |
| -- | --------------------------------------------------------------------------- | ----------------------------------- | ----------------------------------------------------- |
|    | **第 0 层 · 基础**                                                   |                                     |                                                       |
| 0  | [Harness thesis](sections/00-harness-thesis/README.zh-CN.md)                 | agency（能动性）从哪里来？          | Model vs harness, actions, observations, permissions  |
|    | **第 1 层 · 核心 loop**                                              |                                     |                                                       |
| 1  | [Agent loop](sections/01-agent-loop/README.zh-CN.md)                         | agent 怎么持续运作？                | `messages[]`, loop, `stop_reason`                 |
| 2  | [Tool runtime](sections/02-tool-runtime/README.zh-CN.md)                     | 工具怎么被调用和路由？              | Registry, schemas, dispatch, deferred search          |
| 3  | [Permission &amp; sandbox](sections/03-permission-sandbox/README.zh-CN.md)   | 副作用怎么被管控？                  | Permission modes, approvals, sandboxing               |
| 4  | [Hooks](sections/04-hooks/README.zh-CN.md)                                   | 扩展功能怎么挂进 loop？             | `PreToolUse`, `PostToolUse`, lifecycle events     |
|    | **第 2 层 · 复杂工作**                                               |                                     |                                                       |
| 5  | [Planning &amp; todos](sections/05-planning-todos/README.zh-CN.md)           | 大工作怎么拆解？                    | Plan mode, todo list, approval before edits           |
| 6  | [Subagents](sections/06-subagents/README.zh-CN.md)                           | 子问题怎么被隔离？                  | Fresh `messages[]`, delegation, child loop          |
| 7  | [Skills](sections/07-skills/README.zh-CN.md)                                 | 能力怎么按需加载？                  | `SKILL.md`, catalog, progressive disclosure         |
| 8  | [Context management](sections/08-context-management/README.zh-CN.md)         | 长对话怎么控制在 context window 内？ | Budgeting, stubs, compaction, summaries               |
|    | **第 3 层 · 知识与韧性**                                             |                                     |                                                       |
| 9  | [Memory](sections/09-memory/README.zh-CN.md)                                 | 它如何跨运行保留记忆？              | Selection, recall, extraction, consolidation          |
| 10 | [System prompt assembly](sections/10-system-prompt/README.zh-CN.md)          | 每一轮的 prompt 怎么生成？          | Prompt sections, live state, cache boundaries         |
| 11 | [Error recovery](sections/11-error-recovery/README.zh-CN.md)                 | 长任务怎么在失败中存活？            | Retries, overflow recovery, fallback model            |
|    | **第 4 层 · 长时间执行与异步**                                     |                                     |                                                       |
| 12 | [Task system](sections/12-task-system/README.zh-CN.md)                       | 工作怎么跨越单一轮次持续存在？      | Task records, dependencies, locks                     |
| 13 | [Background execution](sections/13-background-execution/README.zh-CN.md)     | 工作怎么在主 loop 之外执行？        | Handles, task state, notification queue               |
| 14 | [Scheduling](sections/14-scheduling/README.zh-CN.md)                         | agent 怎么在之后才执行？            | Cron, sleep, remote triggers, queues                  |
| 15 | [Worktree isolation](sections/15-worktree-isolation/README.zh-CN.md)         | 并行工作怎么避免冲突？              | Git worktrees, cwd binding, safe cleanup              |
|    | **第 5 层 · 多 Agent**                                               |                                     |                                                       |
| 16 | [Coordination](sections/16-coordination/README.zh-CN.md)                     | 多个 agent 怎么沟通？               | Inboxes, broadcasts, permission bubbling              |
| 17 | [Protocols](sections/17-protocols/README.zh-CN.md)                           | agent 怎么达成共识并干净收尾？      | Plan approval, shutdown handshakes                    |
| 18 | [Autonomy](sections/18-autonomy/README.zh-CN.md)                             | agent 怎么自我组织？                | Idle cycle, task claiming, self organization          |
|    | **第 6 层 · 扩展与集成**                                             |                                     |                                                       |
| 19 | [MCP / plugins / channels](sections/19-mcp-plugins-channels/README.zh-CN.md) | harness 怎么连到外面的世界？        | Transports, channels, tool pool assembly              |
| 20 | [Observability &amp; evaluation](sections/20-observability/README.zh-CN.md)  | 我们怎么知道它有效？                | Tracing, metrics, evals, failure analysis             |
| 23 | [Evaluation](sections/23-evaluation/README.zh-CN.md)                         | 怎么知道这次改动有没有让它变好？    | Eval environments, resets, judges, Pass^k             |
|    | **第 7 层 · 组合**                                                   |                                     |                                                       |
| 21 | [Loop engineering](sections/21-loop-engineering/README.zh-CN.md)             | loop 怎么叠成一个能自己运转的系统？ | Verification loop, triggers, budgets, maturity levels |
| 22 | [Graph engineering](sections/22-graph-engineering/README.zh-CN.md)           | 什么时候该让代码决定下一步，而不是问 model？ | Nodes, coded edges, cycles, agents as nodes           |

---

## 文件结构

24 篇章节说明都已备齐，从 `00-harness-thesis/` 一路到 `23-evaluation/`。

```text
learn-agent-architecture/
├── README.md                  # 最上层地图
├── sections/                  # 每个章节一个文件夹
│   ├── 00-harness-thesis/     # 每章一份 README.md
│   ├── 01-agent-loop/src/     # 可执行的代码链从这里开始
│   ├── ...
│   └── 23-evaluation/
└── references/                # 原始出处与前人成果
```

每个章节文件夹都是 `NN-name/` 格式，里面有一份 `README.md`。

第 1 到 23 章还带有可执行的 `src/`。代码一章一章累积上去。
每一章添加一个机制，并让 `loop.py` 演进，所以对比相邻两章的 diff，就能看出改了什么。

超出单一章节篇幅的深入主题，会独立成自己的 repo。
[learn-agent-memory](https://github.com/hardness1020/learn-agent-memory) 把第 9 章的 memory loop 扩展成完整子系统。
[learn-deepseek-harness](https://github.com/hardness1020/learn-deepseek-harness) 从零开始学 deepseek-harness，每次拆解一个 plugin 扩展点。

---

## 执行示例

第 1 到 23 章都附有可执行的示例。从 repo 根目录配置一次就好：

```bash
uv venv
uv pip install -r requirements.txt
cp .env.example .env        # then add your ANTHROPIC_API_KEY
```

固定版本的依赖软件包放在 [`requirements.txt`](requirements.txt)。`.env` 已被 gitignore，内容包含：

- `ANTHROPIC_API_KEY`
- 可选的 `ANTHROPIC_MODEL`
- 可选的 `ANTHROPIC_BASE_URL`

每个可执行的章节都有：

- `test.py`：离线检查，不需要密钥。
- `demo.py`：对 API 的实时示例。

```bash
python sections/01-agent-loop/src/test.py         # offline
uv run python sections/01-agent-loop/src/demo.py  # live
```

---

## 参与贡献

- **添加一个系统。** 把新的 agent 放进同一套章节结构里。
- **深化某一章。** 补上一个机制、更清楚的图，或更精准的出错分析。
- **修正内容。** 这些都是从源代码、文档和实际行为重建出来的。欢迎附上出处的修正。

请优先采用有名字、可查证的机制，而不是臆测。记得引用出处。
完整的 PR 检查列表见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 参考资料

- [claude-code](https://github.com/yasasbanukaofficial/claude-code): Claude Code 源代码备份，用来对照机制名称与实现路径。
- [hermes-agent](https://github.com/NousResearch/hermes-agent): 开源 agent harness（MIT），作为第二个研究系统。
- [mini-swe-agent](https://github.com/swe-agent/mini-swe-agent): 极简 SWE agent（MIT），作为第三个研究系统。
- [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness): 以 plugin 为基础的 agent harness（MIT），作为第四个研究系统。
- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code): 以代码为主的 harness 重建与章节架构。
- [Anthropic Agent Skills 最佳实践](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): skills 的渐进式披露层级。
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): cache 断点、TTL、计价与 token 下限。
- [cobusgreyling/loop-engineering](https://github.com/cobusgreyling/loop-engineering): loop 的组成模块与成熟度分级。
- [LangChain · The art of loop engineering](https://www.langchain.com/blog/the-art-of-loop-engineering): 四层堆叠的 loop。
- [Addy Osmani · Loop engineering](https://addyosmani.com/blog/loop-engineering/): 由模块组合出的 agent loop。
- [MindStudio · What is loop engineering](https://www.mindstudio.ai/blog/what-is-loop-engineering-autonomous-ai-agent-workflows): 自主工作流的目标条件。
- [Lilian Weng · Harness engineering for self-improvement](https://lilianweng.github.io/posts/2026-07-04-harness/): 改进 loop，以及放在 loop 外的把关。
- [LangChain · 3 years of graph engineering](https://www.langchain.com/blog/3-years-of-graph-engineering-with-langgraph): node、edge、cycle，以及把 agent 当 node。
- [Anthropic · Building effective agents](https://www.anthropic.com/engineering/building-effective-agents): workflow 与 agent 的分界，加上五种 workflow 图形。
- [Google · Why we built ADK 2.0](https://developers.googleblog.com/en/why-we-built-adk-20/): 用代码选路，以及 node 之间的 context 隔离。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): 《深入理解 AI Agent》（李博杰著，Apache-2.0）。第 6 章是评估本章的主要出处。
