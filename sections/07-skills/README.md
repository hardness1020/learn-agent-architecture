# 7 · Skills

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> A skill is a self-contained bundle of expertise, instructions plus any scripts and files, loaded only when a task needs it.

A skill turns a general agent into a specialist for one job.
It packages a workflow: the instructions to follow, plus any scripts to run and reference files to consult.
The agent loads a skill only when a task calls for it, so one agent can reach many specialized capabilities without loading them all up front.

Each skill is a folder with a `SKILL.md` file. The frontmatter names and describes the skill.
The body holds the instructions, and the folder can bundle extra scripts and reference files that load only when the skill uses them.

The agent needs to know that skills exist, but it should not pay for every skill body on every turn.

The skill system must:

1. List available skills cheaply.
2. Load full instructions only when a skill is selected.
3. Let skills point to extra files without loading them automatically.
4. Discover skills from built-in, user, project, plugin, or MCP sources.

Without this layer, the prompt is either too large or the agent cannot find its extensions.

---

## Mechanism

![Mechanism diagram](assets/07-skills.png)

Skills use progressive disclosure. The model sees only enough information to decide whether to load more.

1. **Metadata.** `name` and `description` from frontmatter, plus the skill's path. This cheap catalog rides in the system prompt every turn.
2. **Instructions.** The `SKILL.md` body. The model reads the file only when a task needs the skill.
3. **Resources.** Extra files in the skill folder. The model reads them with the same file tool when the instructions point to them.

No skill-specific tool is needed. Once the catalog names each skill and its path,
the agent loads a skill by reading its file with the normal Read tool. L2 and L3 are both just file reads.

The description does most of the work. It is a routing condition, not a summary.
That one line is all the model sees before it decides. So say when to use the skill, and say when not to.
Add one counter-example. A line that only names the topic makes the model guess.

### New: scan the skills and list them in the prompt

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

- `load_skills` scans `SKILL.md` files and keeps only frontmatter for the catalog.
- `catalog_prompt` renders that catalog into the system prompt, one line per skill, with the path to read.
- The body and the resources are plain files. The normal Read tool loads them on demand, so there is no skill-specific tool.
- The Read tool is scoped to the skills directory, so a skill name can never escape into the filesystem.

### New: the store evolves

Loading is half of a skill system. The store also grows and decays (Hermes calls this skill evolution).

Growth is a write. The agent distills a finished workflow into a new skill, so the next run loads instructions instead of rediscovering them:

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

- `WriteSkill` is the model-facing tool around this function. Writing a skill is a side effect, so the section-3 gate asks unless a rule pre-approves it.
- The written file is a normal `SKILL.md`. Nothing special marks it: the next `load_skills` scan catalogs it like a hand-written skill.
- The name is resolved and checked the same way `read_tool` checks paths, so the store cannot be escaped from either direction.

Decay starts with measurement. Loading a skill is the use signal, so `read_tool` records it as a side effect of the read:

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

- The record keys on the skill's folder name, taken from the path the model read. A resource read (L3) does not bump it, only the `SKILL.md` body (L2).
- A skill with no record has `last_used_at` 0, so never-used skills count as stale too.
- `stale_skills` is a report, not an action. Deciding what to do with it is a curator's job; Hermes runs a background curator agent on the same signal (archive, consolidate, pin).
- The data flow is a loop across runs: read bumps `.usage.json`, the curator reads it, the catalog reflects what survives, and `WriteSkill` feeds new entries in.

### How it integrates

The loop does not change. Reading a skill returns a tool result that enters `messages[]`.

The catalog belongs in the system prompt. The body enters the conversation only after the model reads the file. Resource files are read later only if needed.

Because loaded skill text lives in `messages[]`, it can be compacted like any other message when the context fills (section 8).
Keep skill bodies short and point to files for large references.

### Further reading

None of this is in `src/`. It comes from ai-agent-book and vendor docs, and is not confirmed of the systems in the table.

**What the catalog costs.** Progressive disclosure lowers the cost of a large skill store. It does not make it free.
The catalog sits in the prefix, so it is read once at prefill and re-sent on every turn after that.
After the first turn that prefix is cached, so re-sending it is cheap.
A loaded body costs more, and it stays in the window until something compacts it.
So the number to watch is how many skills the catalog carries, not how often bodies get read.

**Where the catalog lives.** The listing can sit in the system prompt, which is what `src/` does.
It can also sit inside the description of one activation tool. The open standard allows both.
The trade-off is where the tokens land. In the system prompt they are part of every session's prefix.
In a tool description the prefix stays smaller, and the model reaches the listing through that tool instead.

**Deferred tool loading.** Tools can use the same pattern, for the same reason: a schema is large and most turns do not need it.
The prefix keeps only tool names and one-line descriptions. The model asks for a full schema when it needs one.
That schema is appended at the end of the context, so the cached prefix is untouched and nothing before it has to be recomputed.
Skills were the first place this repo met progressive disclosure. The book reports the same pattern moving into the tool layer (section 2).

**When to write a skill.** Say a run finishes a long workflow correctly for the first time. Should that become a skill?
The demo here says yes. The workflow finishes, the agent calls `WriteSkill`, and the next scan catalogs it.
That is the smallest rule that shows the loop, and it is what the runnable code does.

**The book's higher bar.** The book says no, because one run is too little evidence.
It asks for four things before a skill becomes a formal capability:

- The same pattern in at least two runs that did not fail.
- A check that does not come from the run that proposed the skill. Voyager works this way: a skill enters the library after the environment confirms it.
- A search of the store first. If something close already exists, patch it instead of adding a duplicate.
- The pitfalls the run hit, kept in the body, not just the path that worked.

**Which rule to pick.** Both are defensible, because they answer different questions.
First success teaches the mechanism and keeps a demo short. A support threshold is what stops a store of hundreds from filling with notes used once.
A candidate step sits between them. The distilled workflow lands as a candidate, not as a catalog entry.
It gets drafted, tested, evaluated, and revised before promotion. Anthropic's Skill Creator runs that loop.
In this section's code it would be a staging folder that `load_skills` skips until a curator promotes it.

**Consolidation runs offline.** The curator is a scheduled pass, not a live one. The book calls it sleep-time learning and gives it five steps:

1. **Trigger.** A schedule, an idle window, or a store that grew past a size limit.
2. **Orient.** Snapshot the store first, so every later step can be rolled back.
3. **Gather and merge.** Read the usage records and recent runs. Fold near-duplicates into one skill. Pull candidates in.
4. **Validate and approve.** Check the merged bodies against the runs that produced them. What fails stays out.
5. **Prune and index.** Archive stale skills by a fixed rule, then rebuild the catalog.

**Why offline matters.** Running the curator offline is itself a safety boundary. The online loop executes and records.
It never edits the store mid-run. So one lucky run cannot promote itself,
and text the agent read from outside cannot become a permanent instruction between turns.

---

## Per system

How each agent describes, triggers, and finds skills.

| | Claude Code | Hermes Agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | Fits a budget. A skill can fork and scope tools. | A curator merges new skills, archives stale ones. | The catalog rides in history and refreshes when it changes. |
| **Cons** | Vague descriptions hide skills. | Automatic changes need pins and staged approvals. | Catalog rewrites add messages to history. |
| **Why** | Skills fork and scope tools, so a file read is not enough. | Loading is half the job; the store must grow and decay. | Skills change while a session runs. |
| **How: skill format** | `SKILL.md` folder; frontmatter can limit tools. | Same shape, sorted into category folders. | Bundles or flat files. Frontmatter sets who may invoke. |
| **How: load trigger** | A `Skill` call injects the body; file matches also fire it. | `skill_view` returns the body, bumping use counts. | A tool rereads the body on request. |
| **How: discovery** | Built-in, user, project, plugin, MCP sources. | Bundled, optional, user, plugin, and hub sources. | Providers merge over layered scopes and ranked roots. |

---

## Failure modes

- **Skill never fires.** The description is too vague. Write trigger-shaped descriptions.
- **Catalog gets too large.** Too many skills can crowd the prompt. Keep skills focused and let the loader trim.
- **Body is lost after compaction.** Re-read the skill file or keep the body short.
- **Path traversal.** The catalog hands the model a path. Scope the Read tool to the skills directory so `../` cannot escape it.
- **Forked skill loses live context.** Use forked skills only for self-contained work.
- **Poisoned skill from the supply chain.** An installed third-party skill is outside content, but it loads as instructions.
  That gives it more reach than a poisoned web page, because the catalog already vouches for it.
  Read the body and any bundled scripts before install. Pin the version. Review again on update.
- **Injected text becomes permanent.** A prompt injection in `messages[]` dies with the session. The same text written into `SKILL.md` loads on every later run.
  So never let unreviewed outside content reach `WriteSkill`. Hold new skills as candidates until a separate pass approves them.
  Never let a skill edit that approval gate.
- **Use counts overstate learning.** Loading a skill is not following it. A load count says the catalog routed.
  It does not say the skill changed the outcome. Track two numbers instead: did the skill fire, and did the run get better.

---

## Runnable

[`src/`](src/) carries 06 forward and adds:

- [`skills.py`](src/skills.py): catalog scan, the system-prompt listing, a path-scoped `Read` tool, and the evolution half (`WriteSkill`, `record_use`, `stale_skills`).
- `skills/<name>/SKILL.md`: sample skills, including one with a resource file.
- [`loop.py`](src/loop.py): unchanged because loading a skill is just a file read.
- [`test.py`](src/test.py): checks catalog scan, the prompt listing, file loads, path-traversal rejection, usage bumps, staleness, and an agent-written skill entering the catalog.
- [`demo.py`](src/demo.py): the agent uses a skill, then saves a new one; the closing scan shows the store grew.

```bash
python sections/07-skills/src/test.py         # offline checks, no key
uv run python sections/07-skills/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code):
  `skills/loadSkillsDir.ts`, `skills/bundledSkills.ts`, `skills/mcpSkillBuilders.ts`, `tools/SkillTool/SkillTool.ts`, `tools/SkillTool/prompt.ts`.
- [Hermes Agent source](https://github.com/NousResearch/hermes-agent):
  `tools/skills_tool.py` (`skills_list`, `skill_view`), `tools/skill_usage.py`, `hermes_cli/curator.py`, `tools/skills_hub.py`, `tools/skills_ast_audit.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/skill/skill/src/index.ts`, `packages/skill/skill-filesystem/src/index.ts`, `packages/skill/tool-skill/src/index.ts`,
  `docs/subsystems/skills.md`, `docs/tool-catalog.md`.
- [Anthropic Agent Skills best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices): progressive disclosure levels.
- [learn-claude-code · s07_skill_loading](https://github.com/shareAI-lab/learn-claude-code): section framing.
- [ai-agent-book](https://github.com/bojieli/ai-agent-book): `book/chapter2.md`, `book/chapter8.md`, Chinese original canonical.
- [Agent Skills open standard](https://agentskills.io): catalog placement, system prompt or activation-tool description.
- [Claude Code · prompt caching](https://code.claude.com/docs/en/prompt-caching): where a loaded skill body lands and what it costs.
- [Voyager](https://arxiv.org/abs/2305.16291): a skill enters the library only after the environment verifies it.
- [Anthropic Skill Creator](https://github.com/anthropics/skills): draft, test, evaluate, revise before promotion.
- Lin et al., [arXiv:2605.30621](https://arxiv.org/abs/2605.30621), via the book: whether an update lands and whether it helps are separate measurements.
