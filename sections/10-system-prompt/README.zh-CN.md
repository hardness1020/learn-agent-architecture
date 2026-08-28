# 10 · System prompt assembly

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 每一轮都根据当前状态，重新生成真正需要的 system prompt。

system prompt 是 agent 的常驻指令集，会说明身分、规则、可用工具、项目 context，以及目前启用的功能。

在实际的 agent 系统中，它不能只是一段写死的文字。

工具、memory、输出风格、MCP server 和执行模式都可能随 session 改变，prompt 必须反映当下真正启用的内容。

一个 prompt 组装器解决三个问题：

1. 每项功能都有固定的位置加入自己的指令。
2. 未启用的功能不会出现在 prompt 中。
3. 稳定不变的段落可以使用 prompt caching。

没有明确的组装机制，prompt 很快就会变得过时、臃肿，也难以安全修改。

---

## 核心机制

![机制图](assets/10-system-prompt-assembly.png)

把 prompt 拆成一组命名段落。有些内容固定不变，有些会根据实时状态产生；不适用的段落直接返回 `None`。

组装流程很单纯：按顺序解析每个段落，略过 `None`，再把剩下的内容串起来。

```python
sections = [
    intro, system_rules, doing_tasks, tools_section,
    session_guidance(), memory(), env_info(),
    output_style(), mcp_instructions(),
]
prompt = [s for s in resolve(sections) if s is not None]
```

两条规则让它保持可控：

1. 依状态纳入段落，不要靠关键字猜测。
2. 让易变的内容远离稳定的 prompt 前缀。

### 本章添加：段落与组装

```python
@dataclass
class Section:                                          # src/prompt.py
    name: str
    compute: Callable    # (state) -> str | None ; static sections ignore state

def static(name, text) -> Section:
    return Section(name, lambda _state: text)

def assemble(sections, state) -> str:                  # the prompt for this turn
    parts = (s.compute(state) for s in sections)
    return "\n\n".join(p for p in parts if p is not None)
```

每个段落要不要出现在 prompt 里，是它自己看状态决定的。`compute` 返回 `None` 就略过：

```python
DEMO_SECTIONS = [
    static("intro", "You are a tiny agent. ..."),
    Section("tools", lambda s: "Tools: " + ", ".join(s["tools"]) if s.get("tools") else None),
    Section("env", lambda s: f"cwd: {s['cwd']}" if s.get("cwd") else None),
    Section("mcp", lambda s: "MCP servers connected; ..." if s.get("mcp") else None),
]
```

第 9 章回想出的记忆不放进 system prompt，而是用一则 `<system-reminder>` 消息注入对话。这样 prompt 前缀不会跟着记忆变动，cache 比较守得住。

### Prompt caching

大多数 system prompt 段落在一次 session 中是稳定的。demo 设了一个顶层的 cache 断点：

```python
client.messages.create(model=MODEL, system=assemble(DEMO_SECTIONS, state),
                       messages=messages, cache_control={"type": "ephemeral"})
```

稳定的内容应该排在易变的内容之前。如果一个会变动的值出现在前面，它可能会让更多 cache 失效。

这条规则之所以严格，是因为价目表。cache 是拿 token 前缀精确比对的。
前面改掉一个 token，它后面所有 cache 过的 token 就全部作废。读 cache 大约只要新鲜 input token 的十分之一，写 cache 反而比新鲜的还贵。
所以挪动一个字，就可能让原本吃得到 cache 的调用变成全价。
最常见的原因有两个：prompt 开头附近印了时间戳或 token 数，以及工具列表的顺序每次跑都不一样。

Claude Code 也使用一个明确的动态边界。当较小的动态尾段变动时，这能保护一大段静态前缀。

### 如何集成到现有架构

loop 会在每次模型调用前生成 prompt：

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)
    system = prompt(registry, session) if prompt else None   # 10 · assemble from live state
    response = model(messages, registry, system)
    ...
```

- `prompt` 是一个闭包住段落列表的可调用对象。
- 它读取实时状态，例如启用中的工具和 session 模式。
- 传入 `prompt=None` 会维持第 9 章的行为。

### 对照：段落 registry

上面那份列表写死在一个文件里。要加段落就得改那个文件，而文件里的先后顺序就是 prompt 的顺序。

deepseek-harness 则改为通过注册表组装 prompt。每个 plugin 注册一个命名段落，再用数字决定排列位置。
数字照惯例分成几个区段：harness 身分最前面，接着是部署方的 persona，再来才是工具指引。
组装时照数字排序，所以 plugin 不必知道别人注册了什么，也能找到自己的位置。

registry 还带来两条规则。

- 某个 agent 可以用一个已经存在的名字，注册自己的段落。那个 agent 看到的是自己那份，其他人照样用共享的。
- 段落文字里可以放 `{{variables}}`，而且 render 很严格。名字对不上就直接报错，不会让 prompt 带着一个洞送出去。

会变的事实不放进这份 prompt。它们以快照的形式接到对话后面，而且只有 render 出来的文字真的变了才接，前缀因此对 cache 友善。

[`src/registry.py`](src/registry.py) 就是这件事的精简版。它是对照用的 demo，没有接进 `assemble()`，所以后面的章节照样沿用同一套 prompt 代码。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book，也未经下面表格的系统证实。

**边界前面每加一个条件，前缀就多一份：**边界之前放一个看执行期状况决定的条件，cache 就得存两份前缀，条件的每种结果各一份。
三个条件变八份。十个条件超过一千份，而且每一份都要各自预热，等于几乎每个 session 都是冷启动。
把有条件的段落挪到边界之后，前缀就又只剩一份。

**一种任务类型挑一组示例，挑完就别再动：**few-shot 示例也放在前缀里，所以上面那条规则一样管得到它。
每次请求都去挑最相关的示例，等于每次调用都重写前缀，cache 就用不上了。
固定一组示例，贴合度是会差一点，但整个 session 的前缀都是热的。

**状态列让模型知道现在跑到哪：**模型看不到 harness，所以有些 harness 会把执行期状态写成 context 尾端的几行字：

- 已经调用过几次 tool
- 当前的 TODO
- 过了多久
- 工作目录

这几行要一直保持最新，做法有两种，两种都有代价。
每一轮就地换掉这一段，状态就只有一份是真的，但尾巴被重写，它后面的 cache 也没了。
每一轮往后追加一段新的，cache 保得住，但旧的段落会留在历史里，模型可能照着已经变掉的状态行动。
Claude Code 走的是追加，用的就是第 9 章那些 `<system-reminder>` 消息。
不管选哪一种，这段文字都要用程序读真实状态产生。叫 LLM 去摘要，会多一次调用、多一份延迟，还可能写错。

**外面来的文字是数据，不是命令：**抓回来的网页、文件、issue 留言、MCP server 的响应，全都是数据，没有一句是用户说的。
这些文字没有任何标记就送进去，里面某一句长得像指令的话，就会跟 system prompt 平起平坐。这就是 prompt injection。
威胁模型和执行层的解法归第 3 章管：权限和 sandbox 决定一个被挟持的 agent 到底能做什么。
prompt 这一层可以更早一步动手，做法是把指令和数据分开：

- 外部内容包在有标记的区块里，标明它从哪里来。并在 prompt 里讲清楚：标记过的内容是拿来读的数据，不是要照做的指令。
- role 分得干净。指令走 system prompt，结果走 `tool_result` 区块，人讲的话走 user turn。
- 忠诚对象在 prompt 里讲一次：agent 为用户和营运方工作，任何从 tool 进来的文字都改不了这件事。书里把这叫 principal loyalty。

**prompt 这一层不是边界：**模型还是可能被说服而违规，所以第 3 章的检查照样要跑。
prompt 层降低发生概率，执行层限制损害范围。

---

## 不同系统怎么做

每一轮如何生成 prompt。

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | 不会留着过时的指令，工具指引对得上启用中的工具集。 | 只从 config render 一次，没有东西要失效。 | 每一项 prompt 事实都有一个负责人，引用错了会直接报错。 |
| **限制** | 多了段落 registry、cache 失效规则和排序纪律。 | prompt 在 run 中途改不了。 | registry、scope 和排序区段，全都是要维护的机制。 |
| **设计原因** | 工具、记忆和模式会因 session 而异。 | 假设工具集在 run 中途不会变。 | plugin 各自拥有自己的事实，所以 prompt 由多个段落组装而成，不是直接修改字符串。 |
| **做法：assembly point** | 一个 prompt 组装器，每个段落各返回一个字符串。 | config 里的 Jinja2 template，变量缺了会直接报错。 | 一个 registry，加上每个 scope 都能调整的组装事件。 |
| **做法：sections** | 静态与动态段落，项目上下文通过 context 消息传入。 | 两份 template：system 与 instance。 | 命名段落按数字区段排列，scope 可以覆盖同名段落。 |
| **做法：when built** | 每一轮根据实时状态生成，动态段落会被 memoize。 | 只在 run 开始时组一次。 | 每一步组一次。会变的事实改以快照附加。 |

---

## 常见问题

- **易变文字打坏 cache：**把会变动的内容放到后面，或放到 prompt 前缀之外。
- **段落 cache 过时：**当 session 状态改变时，清掉被记忆的段落。
- **Prompt 提到不存在的工具：**从实时启用的工具集生成工具文字。
- **上下文混进 prompt：**当项目文件、日期和 git 状态经常变动时，把它们放进 context 消息。
- **Prompt 覆盖互相冲突：**用单一 resolver 定义优先顺序。
- **cache key 变太多份：**边界之前每多一个条件，要各自预热的前缀就翻倍。有条件的段落一律放到边界之后。
- **状态区块过期：**追加式的状态会越积越多，模型可能照着旧的那一份行动。标清楚哪一份最新，或是就地换掉并接受 cache 重建。
- **外部内容被当成指令：**tool 结果照来源加上标记，并在 prompt 里说明标记过的就是数据。真正的边界仍然是第 3 章的权限检查。

---

## 动手跑跑看

[`src/`](src/) 承接 09 并加入：

- [`prompt.py`](src/prompt.py)：`Section`、`static` 和 `assemble`。
- [`registry.py`](src/registry.py)：deepseek-harness 的对照：段落注册时带一个排序数字，scope 可以盖掉同名段落，`{{variable}}` 严格 render。
- [`loop.py`](src/loop.py)：每一轮重新生成 prompt。
- [`demo.py`](src/demo.py)：加入顶层的 `cache_control`。
- [`test.py`](src/test.py)：检查段落会依状态正确纳入或略过；registry 的检查涵盖排序、同名覆盖，以及变量对不上就报错。

```bash
python sections/10-system-prompt/src/test.py         # offline checks, no key
uv run python sections/10-system-prompt/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：`constants/prompts.ts`、`constants/systemPromptSections.ts`、`utils/api.ts`、`QueryEngine.ts`。
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent)：`config/mini.yaml`、
  `agents/default.py` 的 `_render_template` 与 `get_template_vars`、`models/utils/cache_control.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/core/system-prompt/README.md`、`packages/core/system-prompt/src/index.ts`、`packages/core/agent-loop/src/runtime-context.ts`、
  `docs/subsystems/system-prompt.md`、`docs/agent-lifecycle.md`。
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)：cache 断点、TTL、定价，以及 token 下限。
- [Claude Code prompt caching 文件](https://code.claude.com/docs/en/prompt-caching)：静态前缀与动态尾段之间那道明确的 cache 边界。
- [ai-agent-book · 第 2 章](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md)（《深入理解 AI Agent》，李博杰，以中文原版为准）：
  KV cache 的成本模型、cache 作为架构约束（边界之前的条件会让 cache key 翻倍）、few-shot 的前缀稳定性、
  agent 状态列以及就地替换和往后追加之间的取舍，还有 context 层的注入防御与 principal loyalty。
  书中的状态列与 loyalty 数据都是作者自己的评测，属于单一来源，这里不引用那些数字。
- [learn-claude-code · s10_system_prompt](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
