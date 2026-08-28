# 9 · Memory

[English](README.md) · [繁体中文](README.zh-TW.md) · **简体中文**

> 把值得长期保留的信息存到对话之外，需要时再找回来。

`messages[]` 是单次执行的记忆。它会随着这次执行结束而消失，执行期间也可能因 context 管理而被压缩。

长期 memory 的做法不同：先把重要事实存到对话之外，再于后续轮次中找回与当前任务相关的内容。

记忆必须做到：

1. 判断哪些内容值得长期保存。
2. 把这些内容写到对话之外的存储空间。
3. 每次只找回与当前任务相关的项目。
4. 定期整理过时或重复的信息。

没有 memory，agent 会重复询问相同问题，也记不住不同 session 之间的用户偏好。但如果什么都存，搜索结果又会充满噪声与过时信息。

本章先实现最小可行的 memory loop。到了 production 规模，memory 会成长为独立的子系统，加入 event log、typed record、temporal facts 和 hybrid retrieval。
完整做法收录在 [learn-agent-memory](https://github.com/hardness1020/learn-agent-memory)。

---

## 核心机制

![机制图](assets/09-memory.png)

最小版 memory 由三部分组成：文件存储区、索引，以及按需 recall。

loop 不会一次读完整个存储区，而是先查询成本较低的索引，再加载少数与当前问题最相关的 memory 文件。

关键因此变成「文件要怎么被找到」。recall 只根据每个文件的一行索引文字排序，确定选中后才读取完整内容，所以那一行索引就是 memory 的入口。

一共有四种操作：

- **Selection** 决定要存储什么。只存储那些无法靠 grep、git 或项目文件再次推导出来的事实。
- **Recall** 在查询时执行。它对现有记忆排序，把选中的内文注入这一轮的 `messages[]`：包成 `<system-reminder>` 区块，接在 user 消息前面。
- **Extraction** 在执行结束时执行。它写入新的记忆文件。
- **Consolidation** 很少执行。它合并重复项并清除过时项目。

Recall 只读取。Extraction 只写入。把这两个方向分开，可以避免存储区意外膨胀。

### 本章添加：index、recall、extraction 与 store

存储区是一个放 `.md` 文件的目录。`load_index` 只读取 frontmatter：

```python
def load_index(memory_dir) -> list[Memory]:            # src/memory.py
    mems = []
    for md in sorted(Path(memory_dir).glob("*.md")):
        if md.name == "MEMORY.md":                     # the index file is not a memory
            continue
        meta, _body = _split(md.read_text())           # frontmatter only, never the body
        mems.append(Memory(md.stem, meta.get("type", ""), meta.get("description", ""), md))
    return mems

def manifest(mems) -> str:                             # one cheap line per memory
    return "\n".join(f"- {m.name} ({m.type}): {m.description}" for m in mems)
```

Recall 拿索引对查询排序。离线时，demo 使用字词重叠来计算。上线时，selector 可以直接选择记忆名称：

```python
def recall(mems, query, k=RECALL_K, selector=None) -> list[Memory]:
    if selector is not None:
        chosen = set(selector(manifest(mems), query))  # live: an LLM returns names to inject
        return [m for m in mems if m.name in chosen][:k]
    scored = ((_overlap(query, m), m) for m in mems)
    hits = sorted((s for s in scored if s[0]), key=lambda s: s[0], reverse=True)
    return [m for _score, m in hits[:k]]
```

Extraction 是唯一会让存储区成长的操作：

```python
def extract(memory_dir, messages, extractor) -> list[Path]:
    written = []
    for m in extractor(messages) or []:
        path = Path(memory_dir) / f"{m['name']}.md"
        path.write_text(_render(m))
        written.append(path)
    return written
```

上面的记忆目录放的是提炼过的事实，但它不是唯一的存储区。原始对话历史可以当第二个：把每次执行的文字记下来，之后用关键字搜回来。log 保留所有内容，所以 extraction 漏掉的事实仍然找得到。Hermes 的 `state.db` 就是这种设计。

`log_run` 在执行结束时，把这次执行的文字附加到一个 SQLite FTS5 数据表：

```python
def log_run(db_path, session_id, messages) -> int:     # src/memory.py
    rows = [(session_id, m["role"], t) for m in messages if (t := _text_of(m))]
    con = _db(db_path)                                  # CREATE VIRTUAL TABLE ... USING fts5
    con.executemany("INSERT INTO session_log VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return len(rows)
```

- `_text_of` 把一则消息展平成可搜索的文字：纯字符串直接通过，API 响应只保留 text block。tool-use block 没有文字，会被略过。
- 每一列是 `(session_id, role, content)`。session id 是 lineage 的 key，所以一笔命中可以说出它来自哪一次执行。
- FTS5 内置在 CPython 的 `sqlite3` 里，所以这份 log 不需要额外的依赖软件包。

`search_sessions` 把 log 读回来，排好序，完全不用调用模型：

```python
def search_sessions(db_path, query, k=SEARCH_K) -> list[tuple]:
    words = _words(query)                               # the same tokenizer recall uses
    if not words or not Path(db_path).exists():
        return []
    con = _db(db_path)
    rows = con.execute("SELECT session_id, role, content FROM session_log "
                       "WHERE session_log MATCH ? ORDER BY rank LIMIT ?",
                       (" OR ".join(sorted(words)), k)).fetchall()
    con.close()
    return rows
```

- 查询字词以 `OR` 相连，任何一个字都能命中；`ORDER BY rank`（bm25）把最佳结果排在最前面。
  这就是带模糊排序的关键字回想，跟 Hermes `session_search` 的做法一样。
- `k` 限制返回的列数，理由跟 `RECALL_K` 限制注入记忆一样：精准度优先于数量。
- `search_tool` 把它包成只读的 `SessionSearch` tool，所以要不要查过去的 session，是模型在 turn 进行中自己决定的。
  抽取记忆的 recall 则是 harness 在 turn 开始前决定的。两条路径的差别在于由谁发动。

`Store` 是 loop 操作 memory 的统一接口，现在会在执行结束时同时写入两个存储区：

```python
def write(self, messages) -> list[Path]:               # Store.write, called at run end
    if self.db is not None:
        log_run(self.db, self.session_id, messages)     # everything, searchable later
    return extract(self.root, messages, self.extractor) if self.extractor else []   # the distilled few
```

selector、extractor 和 session db 都是选用的，所以测试可以离线执行。

### 如何集成到现有架构

记忆在 loop 的两端包住它：

```python
if memory is not None:                                 # before the loop
    user_text = messages[-1]["content"]
    recalled = memory.recall(user_text)
    if recalled:
        messages[-1]["content"] = f"<system-reminder>\n{recalled}\n</system-reminder>\n\n{user_text}"
...
if response.stop_reason != "tool_use":
    if memory is not None:
        memory.write(messages)                         # run ends: extract
    return final_text(response)
```

- Recall 在这一轮之前执行一次，并注入被选中的记忆文字。
- Extract 在模型停下且没有再调用工具时执行。
- `memory=None` 会维持第 8 章的 loop 行为。
- 回想的文字会进入 `messages[]`，所以之后 context 管理可以把它压缩。

### 延伸阅读

`src/` 只用一个扁平的目录，靠 glob 扫过去，所以每个文件都自动进到索引里。存储区大到不能整个扫，就得自己把「找得到」这件事补回来。靠三个东西：

- **自己维护一份索引：**用一个文件列出所有记忆，recall 不必走遍整棵树就有东西可以排序。
- **记忆之间互相链接：**一个文件指向相关的文件，recall 就能从已经加载的文件往外跟着链接走。
- **分层摘要：**每一层摘要下面那一层，读的人可以停在查询需要的深度。

OpenViking 的知识库三个都做了，还给每个文件一个 URI。这些 `src/` 都没有实现。

---

## 不同系统怎么做

各 agent 如何存储、回想、抽取和整理记忆。

| | Claude Code | Hermes Agent |
| --- | --- | --- |
| **优点** | recall 判断相关性比单纯的关键字更准。存储区由后台任务清理。 | 记忆一直在 prompt 里，cache 保持有效。session 搜索不需要模型调用。 |
| **限制** | 每次 recall 都多一次模型调用。consolidation 需要另外一套管控。 | 关键字回想不如 LLM 准。中途写入要等下一个 session 才会进 prompt。 |
| **设计原因** | 什么都存，回想就会杂乱，所以 selector 每次只注入少数几个记忆。 | extraction 可能漏掉事实，所以把原始历史留成第二个存储区，随时搜得到。 |
| **做法：store** | 带 frontmatter 的 Markdown 文件。MEMORY.md 是索引，不是记忆内文。 | 两个 markdown 文件（agent 观察和用户轮廓），加一份 SQLite session log。 |
| **做法：recall** | 模型读索引，最多选出 5 个记忆。内文注入时附上新鲜度注记。 | session 开始时把快照冻结进 prompt，过往 session 用关键字搜。 |
| **做法：extraction** | 分叉出的 agent 在执行结束时写入记忆。 | memory tool 在 session 中途把条目写进磁盘。写入可以先暂存等待批准。 |
| **做法：consolidation** | 后台任务负责合并与清理，由时间、session 数量和一个 lock 管控。 | 字符预算爆掉时由模型改写，并追踪失败。 |

---

## 常见问题

- **Recall 漏掉有用的记忆：**调整 selector，并把描述写得具体。
- **Recall 灌爆这一轮：**限制注入记忆的数量，并以精准度为优先。
- **过时记忆被当成事实：**带上存在时间或新鲜度的元数据。
- **存储区变杂乱：**合并重复项与相互矛盾的项目。
- **存储可推导的事实：**不要存储 grep、git 或源代码文件能回答得更好的事实。
- **Extraction 漏掉细节：**压缩可能在 extraction 之前就移除了细微信息。在接近执行结束时抽取，并把重要事实留在文件里。
- **记忆文件没人连得到：**索引没列它，也没有别的文件连过去，recall 就永远碰不到它。这次写入等于白写。
  写文件的那一步就顺手把索引那行写进去，相关的文件也要互相链接。

---

## 动手跑跑看

[`src/`](src/) 承接 08 并加入：

- [`memory.py`](src/memory.py)：一个 `Store`、索引加载、recall、extraction，以及 session log（`log_run`、`search_sessions`、`SessionSearch` tool）。
- [`loop.py`](src/loop.py)：在开头那一轮回想，并在执行结束时抽取。
- [`test.py`](src/test.py)：在一个暂时的存储区上走过这四种操作，接着记录并搜索过去的 session。
- [`demo.py`](src/demo.py)：agent 通过 `SessionSearch` 从某个过去 session 的原始历史找出答案。

```bash
python sections/09-memory/src/test.py         # offline checks, no key
uv run python sections/09-memory/src/demo.py  # live demo, needs a key
```

---

## 参考资料

- [Claude Code 源代码](https://github.com/yasasbanukaofficial/claude-code)：`memdir/findRelevantMemories.ts`、`memdir/memdir.ts`、`services/SessionMemory/sessionMemory.ts`。
- [Claude Code 记忆服务](https://github.com/yasasbanukaofficial/claude-code)：`services/extractMemories/extractMemories.ts`、`services/autoDream/autoDream.ts`。
- [Hermes Agent 源代码](https://github.com/NousResearch/hermes-agent)：`tools/memory_tool.py`、`hermes_state.py`（`SessionDB`）、`tools/session_search_tool.py`、`tools/write_approval.py`。
- [learn-claude-code · s09_memory](https://github.com/shareAI-lab/learn-claude-code)：章节框架。
- [ai-agent-book](https://github.com/bojieli/ai-agent-book)：`book/chapter3.md`，以中文原著为准。文件系统知识库的范式。
- [OpenViking](https://github.com/volcengine/OpenViking)：知识存成带 URI 的文件、分层摘要，以及 wiki 式的交叉链接。
