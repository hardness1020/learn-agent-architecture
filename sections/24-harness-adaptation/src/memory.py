"""Memory (section 9): a durable file store the agent recalls from and writes to.

Introduced in section 9, then carried forward unchanged.

messages[] dies with the run (section 1) and degrades under compaction
(section 8). Memory is the layer outside the conversation: small .md files with
frontmatter, recall that injects only the few relevant bodies into a turn, and
extraction that writes new files at run end. Four operations, never conflated:
  selection    : what is worth keeping. the type taxonomy gates the store, so
                 derivable facts (code, git) are never written.
  recall       : read-only, at query time. rank the index, inject a few bodies.
  extraction   : write-only, at run end. append new files.
  consolidation: rare, background (section 13). dedupe + prune. not shown here.
Mirrors Claude Code's memdir (findRelevantMemories, memoryScan) + extractMemories.

A second recall path searches raw history instead of extracted facts (Hermes
Agent's session_search over state.db, SQLite FTS5): log_run() appends each run's
text to a searchable session log, and search_sessions() returns actual past
messages, ranked, with no model call. Extraction keeps distilled facts; the log
keeps everything, so a fact extraction missed is still findable.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tools import Tool

TYPES = ("user", "feedback", "project", "reference")   # non-derivable only; the rest is grep's job
RECALL_K = 5                                            # cap injected memories (precision over recall)
SEARCH_K = 3                                            # cap returned past messages, same reason


@dataclass
class Memory:
    name: str
    type: str          # one of TYPES
    description: str    # the cheap index line, ranked at recall time
    path: Path          # the .md file; its body is read only when recalled


def load_index(memory_dir) -> list[Memory]:
    """Scan <dir>/*.md, keeping frontmatter only. The cheap manifest, never the bodies."""
    mems = []
    for md in sorted(Path(memory_dir).glob("*.md")):
        if md.name == "MEMORY.md":          # the index file itself is not a memory
            continue
        meta, _body = _split(md.read_text())
        mems.append(Memory(md.stem, meta.get("type", ""), meta.get("description", ""), md))
    return mems


def manifest(mems) -> str:
    """One line per memory: name, type, description. Always-on and cheap (section 10)."""
    return "\n".join(f"- {m.name} ({m.type}): {m.description}" for m in mems)


def recall(mems, query, k=RECALL_K, selector=None) -> list[Memory]:
    """Pick the few memories relevant to `query`. `selector` is the live relevance
    judge (an LLM reading the manifest); without it, fall back to word overlap."""
    if selector is not None:
        chosen = set(selector(manifest(mems), query))     # LLM returns the names to inject
        return [m for m in mems if m.name in chosen][:k]
    scored = ((_overlap(query, m), m) for m in mems)
    hits = sorted((s for s in scored if s[0]), key=lambda s: s[0], reverse=True)
    return [m for _score, m in hits[:k]]


def recall_block(mems, query, k=RECALL_K, selector=None) -> str:
    """The recalled bodies, formatted for injection into a turn. '' if nothing fits."""
    hits = recall(mems, query, k, selector)
    return "\n\n".join(f"[memory · {m.type}] {_split(m.path.read_text())[1]}" for m in hits)


def extract(memory_dir, messages, extractor) -> list[Path]:
    """Run end: the extractor proposes new memories from the transcript; write each.
    The only operation that grows the store. extractor(messages) returns dicts of
    {name, type, description, body}; an empty list writes nothing."""
    written = []
    for m in extractor(messages) or []:
        path = Path(memory_dir) / f"{m['name']}.md"
        path.write_text(_render(m))
        written.append(path)
    return written


def log_run(db_path, session_id, messages) -> int:
    """Run end: append the run's text to the searchable session log. Everything
    with text lands, not just what extraction kept (Hermes indexes all session
    messages into state.db). ponytail: FTS5 ships in CPython's sqlite3."""
    rows = [(session_id, m["role"], t) for m in messages if (t := _text_of(m))]
    con = _db(db_path)
    con.executemany("INSERT INTO session_log VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    return len(rows)


def search_sessions(db_path, query, k=SEARCH_K) -> list[tuple]:
    """Recall actual past messages by keyword, best match first (bm25), zero model
    cost. Returns (session_id, role, content) rows; [] when nothing matches."""
    words = _words(query)
    if not words or not Path(db_path).exists():
        return []
    con = _db(db_path)
    rows = con.execute("SELECT session_id, role, content FROM session_log "
                       "WHERE session_log MATCH ? ORDER BY rank LIMIT ?",
                       (" OR ".join(sorted(words)), k)).fetchall()
    con.close()
    return rows


def search_tool(db_path) -> Tool:
    """SessionSearch: the model-facing handle for search_sessions, so the agent
    decides when past sessions are worth consulting (Hermes' session_search)."""
    def search(a):
        rows = search_sessions(db_path, a["query"])
        if not rows:
            return "no past sessions match"
        return "\n".join(f"[session {sid} · {role}] {content}" for sid, role, content in rows)

    return Tool("SessionSearch", search, is_read_only=True,
                description="Search past session transcripts by keywords; returns matching messages.",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}},
                              "required": ["query"]})


@dataclass
class Store:
    """The handle threaded into the loop (loop.py): recall before the run, extract
    after. `selector` / `extractor` are the live LLM hooks; both optional. With a
    `db`, run end also logs the transcript for cross-session search."""
    root: Path
    selector: Callable | None = None
    extractor: Callable | None = None
    db: Path | None = None
    session_id: str = "session"

    def recall(self, query, k=RECALL_K) -> str:
        return recall_block(load_index(self.root), query, k, self.selector)

    def write(self, messages) -> list[Path]:
        if self.db is not None:
            log_run(self.db, self.session_id, messages)
        return extract(self.root, messages, self.extractor) if self.extractor else []


def _db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS session_log "
                "USING fts5(session_id, role, content)")
    return con


def _text_of(message) -> str:
    """The searchable text of one message: a plain string, or the text blocks of
    an API response. Tool-use blocks carry no text and are skipped."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "\n".join(t for b in content if (t := getattr(b, "text", "")))


def _overlap(query, mem) -> int:
    return len(_words(query) & _words(mem.name + " " + mem.description))


def _words(text) -> set:
    return {w for w in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(w) > 2}


def _render(m) -> str:
    return f"---\ntype: {m['type']}\ndescription: {m['description']}\n---\n{m['body']}\n"


def _split(text):
    """Minimal frontmatter parse into (meta dict, body). Same shape as skills.py."""
    _, frontmatter, body = text.split("---", 2)
    meta = {k.strip(): v.strip() for k, v in
            (line.split(":", 1) for line in frontmatter.strip().splitlines() if ":" in line)}
    return meta, body.strip()
