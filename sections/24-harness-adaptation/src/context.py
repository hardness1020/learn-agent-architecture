"""Context management (section 8): keep messages[] under the window.

Introduced in section 8, then carried forward unchanged.

messages[] only grows (section 1), so every turn runs cheap, near-lossless
passes first and the expensive lossy summary only as a last resort. Order
matters: budget (persist huge results) before micro (stub old result bodies),
summary last. In the Anthropic format a tool result is a block inside a user
message, so the passes walk those blocks. Mirrors Claude Code's per-turn
budget -> micro -> auto sequence in query.ts.
"""
from __future__ import annotations

TOKEN_LIMIT = 1500       # tiny so the demo trips it; real Claude Code uses the model's window
MAX_RESULT_CHARS = 400   # a single tool result over this is persisted to a preview stub
KEEP_RECENT = 4          # tail messages always kept verbatim
PREVIEW_CHARS = 60


def manage(messages, summarizer=None):
    """Run the cheap passes every turn; summarize only if still over the limit."""
    _budget(messages)                                        # persist huge results   (lossless)
    _micro(messages, KEEP_RECENT)                            # stub old result bodies (cheap)
    if summarizer and estimate_tokens(messages) > TOKEN_LIMIT:
        return _auto(messages, KEEP_RECENT, summarizer)      # summarize history (lossy, last resort)
    return messages


def _tool_results(message):
    """The tool_result blocks in a user message, or [] if it has none."""
    content = message.get("content")
    if message.get("role") == "user" and isinstance(content, list):
        return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
    return []


def _budget(messages):
    """A tool result over the cap is persisted; only a preview stays in context."""
    for m in messages:
        for block in _tool_results(m):
            c = block.get("content")
            if isinstance(c, str) and len(c) > MAX_RESULT_CHARS:
                block["content"] = c[:PREVIEW_CHARS] + " ...<persisted-output>"   # full text on disk in real CC


def _micro(messages, keep_recent):
    """Old tool-result bodies become a stub; the model can re-read if it needs them."""
    for m in messages[:len(messages) - keep_recent]:
        for block in _tool_results(m):
            if block.get("content") != "<elided>":
                block["content"] = "<elided>"


def _auto(messages, keep_recent, summarizer):
    """Replace the middle with one summary; keep the first turn and recent tail.

    ponytail: drops the middle and keeps head + tail, nudging the cut forward so
    it never starts on an orphaned tool_result (which must keep its tool_use turn).
    """
    if len(messages) <= keep_recent + 1:
        return messages

    cut = len(messages) - keep_recent
    while cut < len(messages) and _tool_results(messages[cut]):
        cut += 1
    if cut >= len(messages):
        return messages

    summary = {"role": "user",
               "content": f"[summary of {cut} earlier messages] {summarizer(messages)}"}
    return messages[:1] + [summary] + messages[cut:]


def estimate_tokens(messages) -> int:
    return sum(_content_len(m.get("content")) for m in messages) // 4   # ~4 chars per token


def _content_len(content) -> int:
    if isinstance(content, str):
        return len(content)
    total = 0
    for block in content or []:
        if isinstance(block, dict):
            total += len(str(block.get("content", "")))
        else:                                   # SDK block object (text / tool_use)
            total += len(getattr(block, "text", "") or "")
    return total
