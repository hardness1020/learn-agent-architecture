"""Skills (section 7): progressive disclosure of a skill into context.

Introduced in section 7, then carried forward unchanged.

A skill reveals itself in three levels, each loaded only when needed:
  L1 metadata  : name + description (frontmatter), always in the system prompt (cheap).
  L2 body      : the SKILL.md instructions, read on demand with the normal file tool.
  L3 resources : files bundled in the skill folder, read with the same file tool.

No skill-specific tool is needed. Once the catalog (name, description, path) sits
in the system prompt, the agent loads a skill by reading its SKILL.md with the same
Read tool it uses for any file. L2 (body) and L3 (resources) both go through that
one tool. load_skills() scans the dir and keeps only L1; catalog_prompt() renders
the L1 block for the system prompt. Mirrors Claude Code's loadSkillsDir (scans
.claude/skills) and its system-prompt skill listing. Claude Code wraps body-loading
in a SkillTool because its skills also fork and scope tools; the plain mechanism
here does not need one, so a normal path-scoped Read does the job.

The store also evolves (Hermes Agent's skill evolution):
  use    : reading a SKILL.md bumps a usage record (.usage.json), the way Hermes
           bumps view/use counts on every skill_view call.
  write  : WriteSkill lets the agent distill a finished workflow into a new
           skill, so the next run loads instructions instead of rediscovering them.
  curate : stale_skills() reports skills unused past a cutoff; Hermes runs a
           background curator agent on this signal (archive, consolidate, pin).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from tools import Tool

MAX_LISTING_DESC_CHARS = 80   # per-entry cap keeps the catalog cheap (Claude Code uses 250)
USAGE_FILE = ".usage.json"    # per-store usage record; Hermes keeps the same file per skills root
STALE_AFTER = 30 * 86400      # unused this long (or never used) counts as stale

PATH_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}


@dataclass
class Skill:
    name: str
    description: str       # L1: from frontmatter, shown in the catalog
    path: Path            # SKILL.md; the body (L2) and bundled files (L3) are read on demand


def load_skills(skills_dir) -> list[Skill]:
    """L1: scan <skills_dir>/<name>/SKILL.md, keeping frontmatter only (cheap)."""
    skills = []
    for sub in sorted(Path(skills_dir).iterdir()):
        md = sub / "SKILL.md"
        if md.is_file():
            meta, _body = _split(md.read_text())
            skills.append(Skill(meta.get("name", sub.name), meta.get("description", ""), md))
    return skills


def catalog_prompt(skills, base_dir) -> str:
    """L1: the skills block injected into the system prompt at startup, so the model
    knows which skills exist and where to read each one. Name + description + path
    only, never the body. The agent reads the path with its normal Read tool."""
    base = Path(base_dir)
    lines = [f"- {s.name}: {s.description[:MAX_LISTING_DESC_CHARS]} (read {s.path.relative_to(base)})"
             for s in skills]
    return "Available skills (read a skill's path with the Read tool to load it):\n" + "\n".join(lines)


def read_tool(base_dir) -> Tool:
    """The normal file tool. Loads a skill body (L2) or a bundled resource (L3) by
    path. Scoped to base_dir so a skill name can never escape into the filesystem."""
    base = Path(base_dir).resolve()

    def read(a):
        target = (base / a["path"]).resolve()
        if not target.is_relative_to(base):          # reject path traversal (../../etc/passwd)
            raise ValueError(f"path {a['path']!r} escapes the skills dir")
        if target.name == "SKILL.md":                # loading a skill counts as use (Hermes bumps
            record_use(base, target.parent.name)     # .usage.json on every skill_view)
        return target.read_text()                     # tool result -> enters messages[]

    return Tool("Read", read, description="Read a file by its path.",
                input_schema=PATH_SCHEMA, is_read_only=True)


def record_use(skills_dir, name, now=None) -> dict:
    """Bump `name`'s usage record: uses count plus last_used_at. The curator's
    stale timer keys off last_used_at (Hermes: agent/curator.py)."""
    path = Path(skills_dir) / USAGE_FILE
    usage = json.loads(path.read_text()) if path.exists() else {}
    entry = usage.setdefault(name, {"uses": 0})
    entry["uses"] += 1
    entry["last_used_at"] = now if now is not None else time.time()
    path.write_text(json.dumps(usage))
    return entry


def stale_skills(skills_dir, skills, now=None, stale_after=STALE_AFTER) -> list[str]:
    """The curator's input: names unused for stale_after (never used counts too).
    ponytail: a report, not an action; Hermes archives these via a background
    curator agent, with pins protecting skills from auto-archive."""
    path = Path(skills_dir) / USAGE_FILE
    usage = json.loads(path.read_text()) if path.exists() else {}
    now = now if now is not None else time.time()
    return [s.name for s in skills
            if now - usage.get(s.name, {}).get("last_used_at", 0) >= stale_after]


def write_skill(skills_dir, name, description, body) -> Path:
    """The agent distills a finished workflow into a new skill on disk. The next
    load_skills() scan catalogs it, so the store grows from the agent's own work."""
    base = Path(skills_dir).resolve()
    target = (base / name / "SKILL.md").resolve()
    if not target.is_relative_to(base):              # a name can never escape the skills dir
        raise ValueError(f"skill name {name!r} escapes the skills dir")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")
    return target


def write_tool(base_dir) -> Tool:
    """WriteSkill: the model-facing handle for write_skill. A side effect, so the
    gate (section 3) asks unless a rule pre-approves it. Hermes goes further and
    can stage skill writes for async human approval (write_approval.py)."""
    def write(a):
        write_skill(base_dir, a["name"], a["description"], a["body"])
        return f"skill {a['name']!r} saved; the next catalog scan lists it"

    return Tool("WriteSkill", write,
                description="Save a reusable skill (name, description, body) for future runs.",
                input_schema={"type": "object",
                              "properties": {"name": {"type": "string"},
                                             "description": {"type": "string"},
                                             "body": {"type": "string"}},
                              "required": ["name", "description", "body"]})


def _split(text):
    """Minimal SKILL.md parse into (frontmatter dict, body). ponytail: assumes a
    well-formed '---\\n<key: value>...\\n---\\n<body>' header (real CC uses YAML)."""
    _, frontmatter, body = text.split("---", 2)
    meta = {k.strip(): v.strip() for k, v in
            (line.split(":", 1) for line in frontmatter.strip().splitlines() if ":" in line)}
    return meta, body.strip()
