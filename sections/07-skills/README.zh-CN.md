# 7 · Skills

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> skill 把一套专业工作流程包起来，只在任务需要时加载。

skill 可以让通用 agent 在特定任务上具备专业能力。它打包一整套工作流程，包括要遵循的指令、可执行的 script，以及需要参考的文件。
agent 只有在任务用得到时才加载对应的 skill，因此可以拥有大量专门能力，又不必一开始就把所有内容塞进 context。

每个 skill 都是一个文件夹，核心文件是 `SKILL.md`。frontmatter 负责命名与描述，本文放操作指令；文件夹中还能附上 script 和参考资料，需要时再读取。

agent 必须知道有哪些 skill 可用，但不该在每个 turn 都加载所有 skill 的完整内容。

skill 系统必须做到：

1. 用很低的 token 成本列出可用 skill。
2. 只有选中某个 skill 时，才加载完整指令。
3. 允许 skill 引用额外文件，但不预先加载。
4. 从 built-in、user、project、plugin 或 MCP 等来源探索 skill。

没有这一层，prompt 不是塞得太满，就是 agent 根本找不到已经存在的扩展能力。

---

## 核心机制

![机制图](assets/07-skills.png)

skill 使用 progressive disclosure。模型只会看到刚好足够的信息，来决定要不要加载更多。

1. **Metadata：**来自 frontmatter 的 `name` 和 `description`，再加上这个 skill 的路径。这份 catalog 只占少量 token，所以一直放在 system prompt 里。
2. **Instructions：**`SKILL.md` 的本文。只有在某个任务需要这个 skill 时，模型才会去读这个文件。
3. **Resources：**skill 文件夹里的额外文件。指令指向它们时，模型用同一个 file tool 读取。

不需要专门的 skill tool。只要 catalog 列出每个 skill 的名称和路径，agent 就用一般的 Read tool 去读那个文件来加载 skill。L2 和 L3 都只是读档而已。

description 这一行最关键。它是路由条件，不是摘要。
模型在决定要不要加载之前，就只看得到这一行。所以要写清楚什么时候该用，也要写什么时候不该用。
最好再附一个反例。只写个主题名称，模型只能用猜的。

### 本章添加：扫描 skill 并加入 prompt

```python
@dataclass
class Skill:                                   # src/skills.py
    name: str
    description: str                           # L1: frontmatter -> the catalog
    path: Path                                # SKILL.md; the body is read on demand

def load_skills(skills_dir) -> list[Skill]:    # L1: scan <dir>/<name>/SKILL.md at startup
    skills = []
    for sub in sorted(Path(skills_dir).iterdir()):
        meta, _ = _split((sub / "SKILL.md").read_text())   # keep frontmatter, not the body
        skills.append(Skill(meta["name"], meta["description"], sub / "SKILL.md"))
    return skills

def catalog_prompt(skills, base_dir) -> str:   # L1: the block added to the system prompt
    lines = [f"- {s.name}: {s.description} (read {s.path.relative_to(base_dir)})" for s in skills]
    return "Available skills (read a skill's path with the Read tool):\n" + "\n".join(lines)
```

- `load_skills` 扫描 `SKILL.md` 文件，只保留 frontmatter 给 catalog。
- `catalog_prompt` 把这份 catalog 渲染进 system prompt，每个 skill 一行，附上要读取的路径。
- 本文和 resource 都是普通文件。一般的 Read tool 在需要时加载它们，所以不需要专门的 skill tool。
- Read tool 的范围限制在 skills 目录内，所以 skill 名称永远无法逃逸到文件系统其他地方。

### 本章添加：让 skill store 持续演进

skill 系统不是只有加载这件事。skill store 本身也会成长、也会清理过时内容（Hermes 称之为 skill 演化）。

成长靠写入。agent 把一段做完的工作流程沉淀成新的 skill，下一次执行就直接加载指令，不用重新摸索：

```python
def write_skill(skills_dir, name, description, body) -> Path:   # src/skills.py
    base = Path(skills_dir).resolve()
    target = (base / name / "SKILL.md").resolve()
    if not target.is_relative_to(base):              # a name can never escape the skills dir
        raise ValueError(f"skill name {name!r} escapes the skills dir")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")
    return target
```

- `WriteSkill` 是包住这个函数、面向模型的 tool。写入 skill 会改动文件系统，属于有副作用的操作，所以第 3 章的权限闸门默认会先征询用户；只有 allow 规则预先批准过，才会直接放行。
- 写出来的文件就是普通的 `SKILL.md`。没有任何特殊标记：下一次 `load_skills` 扫描会把它当成一般的 skill 编入 catalog。
- 名称的解析和检查方式跟 `read_tool` 检查路径一样，所以不论读或写，都逃不出 skills 目录。

要清理过时内容，得先量测。加载 skill 本身就是使用信号，所以 `read_tool` 在读档的同时顺手记录：

```python
if target.name == "SKILL.md":                # inside read_tool's read()
    record_use(base, target.parent.name)     # loading a skill counts as use
```

```python
def record_use(skills_dir, name, now=None) -> dict:
    path = Path(skills_dir) / USAGE_FILE     # .usage.json, one record per skill
    usage = json.loads(path.read_text()) if path.exists() else {}
    entry = usage.setdefault(name, {"uses": 0})
    entry["uses"] += 1
    entry["last_used_at"] = now if now is not None else time.time()
    path.write_text(json.dumps(usage))
    return entry

def stale_skills(skills_dir, skills, now=None, stale_after=STALE_AFTER) -> list[str]:
    usage = ...                                  # load .usage.json, default {}
    return [s.name for s in skills
            if now - usage.get(s.name, {}).get("last_used_at", 0) >= stale_after]
```

- 这笔记录以 skill 的文件夹名称为 key，取自模型读取的路径。读 resource（L3）不会累计，只有读 `SKILL.md` 本文（L2）才算。
- 没有记录的 skill，`last_used_at` 是 0，所以从未用过的 skill 也算 stale。
- `stale_skills` 是一份报告，不是一个动作。要怎么处理是 curator 的工作；Hermes 用一个后台 curator agent 处理同样的信号（归档、整并、钉选）。
- 数据会形成一个跨多次执行的 loop：读取操作更新 `.usage.json`，curator 再读取它，catalog 反映保留下来的 skill，`WriteSkill` 则加入新条目。

### 如何集成到现有架构

loop 不用改。读取 skill 就是一次普通的工具调用，tool 结果照样进入 `messages[]`。

三层各有位置：catalog 放在 system prompt。skill 本文要等模型读了 `SKILL.md`，才会进到对话里。resource 文件则等到真的用到时才读。

加载后的 skill 文字就在 `messages[]` 里，所以之后 context 不够用时，它会跟其他消息一起被压缩（第 8 章）。skill 本文要写短，大型参考资料改成指向文件。

### 延伸阅读

以下设计 `src/` 都没有实现，出自 ai-agent-book 和厂商文件，也未经下面表格的系统证实。

**catalog 要花多少成本：**progressive disclosure 让一个很大的 skill store 变便宜，但没有让它变免费。
catalog 就在 prefix 里，prefill 时要被读一次，之后每个 turn 都要再送一次。
第一个 turn 之后那段 prefix 就被缓存住，所以重送的成本很低。
加载本文比较贵，而且本文会一直占着 context，直到有东西来压缩它。
所以真正要盯的数字是 catalog 挂了几个 skill，而不是本文被读了几次。

**catalog 放在哪：**这份列表可以放在 system prompt，`src/` 就是这样做的。
它也可以放进某个用于启用 skill 的 tool description 中，open standard 同时允许这两种方式。
差别在这笔 token 算到哪边。放 system prompt，它就是每个 session prefix 的一部分。
放进 tool description，prefix 就小一些，模型改成通过那个 tool 去看这份列表。

**Deferred tool loading：**tool 也可以用同一套做法，理由也一样：schema 很大，但大部分 turn 根本用不到。
prefix 里只留 tool 名称和一行描述，模型要用到某个 tool 时，才去要完整 schema。
要来的 schema 接在 context 尾端，所以缓存住的 prefix 完全没被动到，前面的东西也都不用重算。
skill 是这个 repo 第一次碰到 progressive disclosure 的地方；照书上说，同一套做法现在也长到 tool 这一层了（第 2 章）。

**什么时候该写一个 skill：**假设某一次执行第一次把一段长流程跑对了，这该不该存成 skill？
这里的 demo 说该。流程一跑完，agent 就调用一次 `WriteSkill`，下一次扫描把它编进 catalog。
这是能把整个 loop 演出来的最小规则，也是可执行代码实际在做的事。

**书里的门槛更高：**书的答案是不该，因为一次执行不算证据。
要让一个 skill 变成正式能力，书要求四件事：

- 同一个模式至少在两趟没有失败的执行里出现过。
- 验证那一步不能来自提出这个 skill 的那趟执行。Voyager 也是这样做：环境确认过，skill 才进得了 library。
- 动手写之前先搜一下 store。如果已经有很像的，就去改它，不要再多开一个重复的。
- 这次踩到的坑要留在本文里，不要只留走得通的那条路。

**该选哪一种：**两种都说得通，因为它们回答的不是同一个问题。
一次成功比较好教机制，demo 也短。门槛则是在 store 长到几百个的时候，挡住那些只用过一次的笔记。
中间还可以插一个 candidate 步骤。沉淀出来的流程先落成 candidate，不直接进 catalog。
它会经过起草、测试、评估、修订，才被升级。Anthropic 的 Skill Creator 就是跑这个 loop。
放到本章的代码里，就是多一个暂存文件夹，`load_skills` 先跳过它，等 curator 升级才收。

**整并是离线做的：**curator 是调度跑的，不是实时跑的。书里叫它 sleep-time learning，分成五步：

1. **触发：**调度时间到、系统闲置，或 store 大小超过上限。
2. **定位：**先对 store 做一次快照，后面每一步才都能回滚。
3. **搜集与合并：**读使用记录和最近几次执行，把几乎重复的 skill 并成一个，再把 candidate 收进来。
4. **验证与批准：**拿产生它们的那几次执行，去检查合并后的本文。没过的就不收。
5. **修剪与建索引：**依固定规则归档过期的 skill，然后重建 catalog。

**为什么一定要离线：**把 curator 放在离线跑，本身就是一条安全边界。在线 loop 只负责执行和记录，跑到一半绝不去动 store。
所以一次刚好成功的执行没办法把自己升级，agent 从外面读进来的文字，也没办法在两个 turn 之间变成永久指令。

---

## 不同系统怎么做

各 agent 如何描述、触发并找到 skill。

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **优点** | catalog 有预算上限。skill 能 fork，还能限制 tool。 | curator 会整并新 skill、归档过期的。 | catalog 放在对话历史里，session 续跑后照样在，内容一变就换新的。 |
| **限制** | 描述太含糊，模型就不会去加载。 | 自动改动需要钉选和暂存批准来把关。 | 每次换掉 catalog 都会往历史里多塞消息。 |
| **设计原因** | skill 还要 fork、还要限制 tool，单纯读档不够用。 | 加载只是一半，store 本身还要能成长、能清理过时内容。 | session 跑到一半，skill 就可能变了。 |
| **做法：skill format** | `SKILL.md` 文件夹，frontmatter 还能限制可用的 tool。 | 同样的形式，依分类文件夹整理。 | 一个文件夹或一个扁平文件。谁能调用写在 frontmatter。 |
| **做法：load trigger** | invoke `Skill` tool 注入本文；动到符合的文件也会触发。 | `skill_view` 返回本文，并累计使用次数。 | 要用到的时候，一个 tool 才读取本文。 |
| **做法：discovery** | built-in、user、project、plugin、MCP 来源。 | bundled、optional、user、plugin、hub 来源。 | 注册的 provider 叠在分层的 scope 上，根目录有排名。 |

---

## 常见问题

- **skill 从不触发：**描述太含糊。把触发条件直接写进描述里。
- **catalog 变得太大：**skill 太多会挤爆 prompt。让 skill 保持聚焦，并让 loader 做裁剪。
- **压缩后本文遗失：**重新读取该 skill 文件，或让本文保持简短。
- **Path traversal：**catalog 会把路径交给模型。把 Read tool 的范围限制在 skills 目录，让 `../` 无法逃出去。
- **forked skill 失去实时 context：**只在自成一体的工作上使用 forked skill。
- **供应链里的毒 skill：**装进来的第三方 skill 本质是外部内容，却是当成指令加载的。
  这比一个被下毒的网页还危险，因为 catalog 已经替它背书了。安装前先把本文和附带的 script 都读过，版本要钉住，更新时再看一次。
- **注入的文字变成永久的：**`messages[]` 里的 prompt injection，session 结束就没了；同一段文字写进 `SKILL.md`，之后每次执行都会加载。
  所以没审过的外部内容，绝不能喂给 `WriteSkill`。新 skill 先当 candidate 放着，等另一道流程批准。也绝不让 skill 去改那道批准闸门。
- **使用次数会高估学习成效：**加载不等于照做。次数只说明 catalog 路由对了，不代表这个 skill 改变了结果。
  要看两个数字：skill 有没有被触发，以及那次执行有没有变好。

---

## 动手跑跑看

[`src/`](src/) 沿用 06 并加上：

- [`skills.py`](src/skills.py)：catalog 扫描、system prompt 列表、限定范围的 `Read` tool，以及演化那一半（`WriteSkill`、`record_use`、`stale_skills`）。
- `skills/<name>/SKILL.md`：示例 skill，包含一个带有 resource 文件的 skill。
- [`loop.py`](src/loop.py)：未变动，因为加载一个 skill 只是读一个文件。
- [`test.py`](src/test.py)：检查 catalog 扫描、prompt 列表、文件加载、path traversal 的拒绝、使用计数、staleness，以及 agent 写出的 skill 进入 catalog。
- [`demo.py`](src/demo.py)：agent 用了一个 skill，接着存下一个新的；收尾的扫描显示 store 长大了。

```bash
python sections/07-skills/src/test.py         # offline checks, no key
uv run python sections/07-skills/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：
  `skills/loadSkillsDir.ts`、`skills/bundledSkills.ts`、`skills/mcpSkillBuilders.ts`、`tools/SkillTool/SkillTool.ts`、`tools/SkillTool/prompt.ts`。
- [Hermes Agent 源代码](https://github.com/NousResearch/hermes-agent)：
  `tools/skills_tool.py`（`skills_list`、`skill_view`）、`tools/skill_usage.py`、`hermes_cli/curator.py`、`tools/skills_hub.py`、`tools/skills_ast_audit.py`。
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness)（`dsh-v0.1.0-rc.7`）：
  `packages/skill/skill/src/index.ts`、`packages/skill/skill-filesystem/src/index.ts`、`packages/skill/tool-skill/src/index.ts`、
  `docs/subsystems/skills.md`、`docs/tool-catalog.md`。
- [Anthropic Agent Skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)：progressive disclosure 的层级。
- [learn-claude-code · s07_skill_loading](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter2.md`、`book/chapter8.md`，以中文原版为准。
- [Agent Skills open standard](https://agentskills.io)：catalog 放哪里，system prompt 或启用用 tool 的 description。
- [Claude Code · prompt caching](https://code.claude.com/docs/en/prompt-caching)：加载的 skill 本文会落在哪里、成本是多少。
- [Voyager](https://arxiv.org/abs/2305.16291)：环境验证过，skill 才进得了 library。
- [Anthropic Skill Creator](https://github.com/anthropics/skills)：升级之前先起草、测试、评估、修订。
- Lin et al.，[arXiv:2605.30621](https://arxiv.org/abs/2605.30621)，转引自书：更新有没有落地、有没有帮上忙，是两个要分开量的数字。
