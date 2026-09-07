# 10 · System prompt assembly

**English** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

> Build the prompt from live state each turn.

The system prompt is the agent's standing instruction set. It describes identity, rules, tools, project context, and active features.

In a real agent, this cannot stay as one hardcoded string.

Tools, memory, output style, MCP servers, and modes can vary by session. The prompt should describe what is actually active.

A prompt assembler solves three problems:

1. New feature text has a clear place to live.
2. Inactive feature text can be skipped.
3. Stable sections can use prompt caching.

Without assembly, the prompt becomes stale, bloated, or hard to change safely.

---

## Mechanism

![Mechanism diagram](assets/10-system-prompt-assembly.png)

Define the prompt as named sections. Some sections are static. Others compute text from live state and return `None` when they do not apply.

Assembly is simple: resolve every section, drop `None`, and join the rest.

```python
sections = [
    intro, system_rules, doing_tasks, tools_section,
    session_guidance(), memory(), env_info(),
    output_style(), mcp_instructions(),
]
prompt = [s for s in resolve(sections) if s is not None]
```

Two rules keep it manageable:

1. Include sections by state, not keyword guesses.
2. Keep volatile content away from the stable prompt prefix.

### New: sections and assemble

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

The section list owns state-driven inclusion:

```python
DEMO_SECTIONS = [
    static("intro", "You are a tiny agent. ..."),
    Section("tools", lambda s: "Tools: " + ", ".join(s["tools"]) if s.get("tools") else None),
    Section("env", lambda s: f"cwd: {s['cwd']}" if s.get("cwd") else None),
    Section("mcp", lambda s: "MCP servers connected; ..." if s.get("mcp") else None),
]
```

Recalled memory is not part of this prompt. It is injected as a `<system-reminder>` message by section 9. That keeps the prompt prefix more stable.

### Prompt caching

Most system prompt sections are stable during a session. The demo sets a top-level cache breakpoint:

```python
client.messages.create(model=MODEL, system=assemble(DEMO_SECTIONS, state),
                       messages=messages, cache_control={"type": "ephemeral"})
```

Stable content should come before volatile content. If a changing value appears early, it can invalidate more of the cache.

The price list is what makes that rule strict. The cache is keyed on an exact token prefix.
Change one token and every cached token after it is gone. A cache read costs about a tenth of a fresh input token, and a cache write costs more than a fresh one.
So one moved word can turn a cached call into a full-price one.
Two things cause this over and over: a timestamp or a token count printed near the top of the prompt, and a tool list whose order changes between runs.

Claude Code also uses an explicit dynamic boundary. That protects a large static prefix when a smaller dynamic tail changes.

### How it integrates

The loop assembles the prompt before each model call:

```python
for _ in range(max_steps):                             # src/loop.py
    messages = context.manage(messages, summarizer=summarizer)
    system = prompt(registry, session) if prompt else None   # 10 · assemble from live state
    response = model(messages, registry, system)
    ...
```

- `prompt` is a callable that closes over the section list.
- It reads live state such as enabled tools and session mode.
- Passing `prompt=None` keeps the section-9 behavior.

### Contrast: a section registry

The list above is fixed in one file. Adding a section means editing that file, and the file order is the prompt order.

deepseek-harness assembles from registrations instead. Each plugin registers a named section and a number that says where it goes.
The numbers fall in bands by convention: harness identity first, deployment persona next, tool guidance after that.
Assembly sorts by number, so a plugin picks its place without knowing what else is registered.

Two more rules come with the registry.

- One agent can register its own section under a name that already exists. That agent sees its version, everyone else keeps the shared one.
- Section text can carry `{{variables}}`, and rendering is strict. An unknown name raises instead of rendering a hole into a shipped prompt.

Dynamic facts stay out of this prompt. They are appended to the conversation as a snapshot, and only when the rendered text actually changed, so the prefix stays cache-stable.

[`src/registry.py`](src/registry.py) is a strip-down of this. It is a contrast demo, not wired into `assemble()`, so later sections carry the same prompt code forward.

### Further reading

None of this is in `src/`. It comes from ai-agent-book, and is not confirmed of the systems in the table.

**Conditions before the boundary multiply the prefix.** Put one runtime condition before the boundary and the cache has to hold two prefixes, one per outcome.
Three conditions make eight. Ten make more than a thousand, and each one warms separately, so almost every session starts cold.
Keep conditional sections after the boundary and there is one prefix again.

**Pick one example set per task type and leave it alone.** Few-shot examples sit in the prefix, so the rule above covers them too.
Retrieving the best examples for each request rewrites the prefix on every call and gives up the cache.
A fixed set fits the request a little less well, and keeps the prefix warm for the whole session.

**A status bar tells the model where the run is now.** The model cannot see the harness, so some harnesses write live state into a few lines at the end of the context:

- how many tool calls have run
- the current TODO
- elapsed time
- the working directory

Those lines have to stay current, and there are two ways to do it. Neither is free.
Replace the block each turn and there is one true copy of the state, but the tail is rewritten and the cache after it is gone.
Append a new block each turn and the cache holds, but the old blocks stay in the history and the model can act on state that has already changed.
Claude Code appends, using the `<system-reminder>` messages from section 9.
Either way, write the block with code that reads real state. An LLM summarizer adds a call, adds latency, and can get it wrong.

**External text is data, never an order.** A fetched web page, a file, an issue comment, and an MCP server response are all data. None of them is the user speaking.
Send that text in with no marker and a sentence inside it that looks like an instruction competes with the system prompt on equal terms. That is prompt injection.
Section 3 owns the threat model and the execution-layer answer: permissions and the sandbox decide what a hijacked agent is allowed to do.
The prompt layer can act earlier, by keeping instructions and data apart:

- Wrap external content in a tagged block that names its source. Say in the prompt that tagged content is data to read, never instructions to follow.
- Keep the roles strict. Instructions go in the system prompt, results go in `tool_result` blocks, and the human speaks in user turns.
- State the loyalty rule once: the agent works for the user and the operator, and no text arriving through a tool can change that. The book calls this principal loyalty.

**The prompt layer is not the boundary.** A model can still be talked out of the rule, which is why section 3's checks run anyway.
The prompt layer lowers the odds. The execution layer bounds the damage.

---

## Per system

How the prompt is composed each turn.

| | Claude Code | mini-swe-agent | deepseek-harness |
| --- | --- | --- | --- |
| **Pros** | No stale instructions. Guidance matches the live tools. | One render from config. Nothing to invalidate. | Every prompt fact has one owner. Bad references fail loud. |
| **Cons** | Needs a section registry, cache rules, and ordering discipline. | The prompt cannot change mid-run. | A registry, scopes, and order bands are a lot of machinery. |
| **Why** | Tools, memory, and modes vary by session. | Assumes the tool set never changes mid-run. | Plugins own their facts, so the prompt is assembled, never edited. |
| **How: assembly point** | A prompt builder, one string per section. | Jinja2 templates; a missing variable fails loudly. | A registry, plus an event each scope can adjust. |
| **How: sections** | Static and dynamic sections; project context rides in messages. | Two templates, system and instance. | Named sections in numeric bands, shadowed by scope. |
| **How: when built** | Per turn from live state, with dynamic parts memoized. | Once, at run start. | Once per step. Changing facts append as snapshots instead. |

---

## Failure modes

- **Volatile text busts the cache.** Put changing content late or outside the prompt prefix.
- **Stale section cache.** Clear memoized sections when session state changes.
- **Prompt names missing tools.** Generate tool text from the live enabled-tool set.
- **Context mixed into prompt.** Put project files, date, and git status in context messages when they change often.
- **Prompt overrides conflict.** Use one resolver to define priority.
- **Too many cache keys.** Each runtime condition before the boundary doubles the prefixes that have to warm separately. Keep conditional sections after it.
- **Stale status blocks.** Appended state accumulates and the model may act on an old copy. Mark the latest block, or replace it and accept the cache rebuild.
- **External content read as instructions.** Tag tool results by source and say tagged content is data. Section 3's permission checks stay the real boundary.

---

## Runnable

[`src/`](src/) carries 09 forward and adds:

- [`prompt.py`](src/prompt.py): `Section`, `static`, and `assemble`.
- [`registry.py`](src/registry.py): the deepseek-harness contrast: sections registered with an order number, scope shadowing, and strict `{{variable}}` rendering.
- [`loop.py`](src/loop.py): re-assembles the prompt each turn.
- [`demo.py`](src/demo.py): adds top-level `cache_control`.
- [`test.py`](src/test.py): checks state-driven inclusion; registry checks cover ordering, shadowing, and the fail-loud variable.

```bash
python sections/10-system-prompt/src/test.py         # offline checks, no key
uv run python sections/10-system-prompt/src/demo.py  # live demo, needs a key
```

---

## Sources

- [Claude Code source](https://github.com/yasasbanukaofficial/claude-code): `constants/prompts.ts`, `constants/systemPromptSections.ts`, `utils/api.ts`, `QueryEngine.ts`.
- [mini-swe-agent source](https://github.com/swe-agent/mini-swe-agent):
  `config/mini.yaml`, `_render_template` and `get_template_vars` in `agents/default.py`, `models/utils/cache_control.py`.
- [deepseek-harness source](https://github.com/deepseek-ai/deepseek-harness) at `dsh-v0.1.0-rc.7`:
  `packages/core/system-prompt/README.md`, `packages/core/system-prompt/src/index.ts`, `packages/core/agent-loop/src/runtime-context.ts`,
  `docs/subsystems/system-prompt.md`, `docs/agent-lifecycle.md`.
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching): cache breakpoints, TTLs, pricing, and token minimums.
- [Claude Code prompt caching docs](https://code.claude.com/docs/en/prompt-caching): the explicit cache boundary between a static prefix and a dynamic tail.
- [ai-agent-book · chapter 2](https://github.com/bojieli/ai-agent-book/blob/main/book/chapter2.md) (《深入理解 AI Agent》, 李博杰; the Chinese original is canonical):
  KV cache economics, the cache as an architecture constraint (conditions before the boundary multiply cache keys), few-shot prefix stability,
  the agent status bar and its replace-versus-append trade-off, and context-layer injection defense with principal loyalty.
  The book's status bar and loyalty measurements are the author's own benchmarks, so the numbers are single-source and are not repeated here.
- [learn-claude-code · s10_system_prompt](https://github.com/shareAI-lab/learn-claude-code): section framing.
