"""Worktree isolation (section 15): each unit of parallel work gets its own
checkout of the repo on its own branch, so concurrent agents cannot clobber a
shared tree. Introduced in section 15, then carried forward unchanged.

A git worktree is a second checkout of the same repo on its own branch. We bind
each unit of work to one by scoping its working directory through a contextvar,
the synchronous analog of Claude Code's AsyncLocalStorage runWithCwdOverride:
every tool that reads get_cwd() inside that scope resolves paths in its own
worktree, so siblings running concurrently never touch the same files (no global
chdir to corrupt a neighbour). On teardown a change count decides: a clean
worktree is removed, a dirty one is kept for review so work is never silently
discarded.

ponytail: change count is `git status --porcelain` (uncommitted files); add
`git log <base>..HEAD` to also keep worktrees that carry unmerged commits.
"""
from __future__ import annotations

import contextlib
import contextvars
import re
import subprocess
from pathlib import Path

_cwd: contextvars.ContextVar = contextvars.ContextVar("cwd", default=None)
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def get_cwd():
    """The working directory bound to this context, or None for the process cwd.
    A tool passes this to subprocess so each agent resolves paths in its worktree."""
    return _cwd.get()


@contextlib.contextmanager
def cwd_override(path):
    """Bind get_cwd() to `path` for this context (and its descendants). Scoped by
    a contextvar, not os.chdir, so concurrent agents never corrupt each other."""
    token = _cwd.set(str(path))
    try:
        yield
    finally:
        _cwd.reset(token)


def validate_slug(slug):
    """Reject path traversal before any git command or path join: '../target'
    would escape .claude/worktrees once normalized. Runs at the trust boundary."""
    if slug in (".", "..") or not SLUG_RE.match(slug or ""):
        raise ValueError(f"bad worktree slug: {slug!r}")
    return slug


def _path(repo_root, slug):
    return Path(repo_root) / ".claude" / "worktrees" / validate_slug(slug)


def create(repo_root, slug, base="HEAD"):
    """git worktree add a fresh checkout at .claude/worktrees/<slug> on branch
    worktree-<slug>; returns its path. The slug is validated first (boundary)."""
    path = _path(repo_root, slug)
    _git(repo_root, "worktree", "add", "-B", f"worktree-{slug}", str(path), base)
    return path


def changes(path):
    """Lines git considers dirty in the worktree (uncommitted files). Zero means
    nothing was written, so the worktree is safe to auto-remove."""
    return len([l for l in _git(path, "status", "--porcelain").splitlines() if l.strip()])


def remove(repo_root, slug, force=False):
    """Tear down a worktree iff it is clean, unless force. A dirty worktree is
    kept for review so parallel work is never silently lost; returns True if removed."""
    path = _path(repo_root, slug)
    if not force and changes(path):
        return False                                    # keep-for-review
    _git(repo_root, "worktree", "remove", "--force", str(path))
    _git(repo_root, "branch", "-D", f"worktree-{slug}")
    return True


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=True).stdout.strip()
