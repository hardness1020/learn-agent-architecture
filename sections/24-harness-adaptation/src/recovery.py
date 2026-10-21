"""Error recovery (section 11): wrap the model call so transient failures retry,
recoverable ones adapt, and only fatal ones surface.

Introduced in section 11, then carried forward unchanged.

with_retry classifies each failure and takes the matching path:
  transient (429 / 408 / 409 / 5xx) : exponential backoff + jitter, honor retry-after.
  overflow  (prompt too long)       : run on_overflow() once (compact, section 8), retry.
  529 storm                         : after MAX_529_RETRIES, raise FallbackTriggered.
  fatal     (4xx, unknown)          : raise at once.
State (consecutive 529s, a one-shot overflow flag) persists across attempts so
each path is bounded. Mirrors Claude Code's withRetry + getRetryDelay +
FallbackTriggeredError + the prompt_too_long -> reactive-compact path.
"""
from __future__ import annotations

import time
from random import random

BASE_DELAY = 0.5            # seconds; doubles each attempt
MAX_DELAY = 32.0
DEFAULT_MAX_RETRIES = 10
MAX_529_RETRIES = 3         # consecutive overloads before falling back to another model
RETRY_STATUS = {408, 409, 429}   # these plus any 5xx are worth retrying


class FallbackTriggered(Exception):
    """Raised after repeated 529s so the caller can retry on `fallback_model`."""

    def __init__(self, fallback_model):
        super().__init__(f"falling back to {fallback_model}")
        self.fallback_model = fallback_model


def should_retry(status) -> bool:
    return status in RETRY_STATUS or (status is not None and 500 <= status < 600)


def retry_delay(attempt, retry_after=None) -> float:
    """Exponential backoff with up to 25% jitter; a server retry-after wins."""
    if retry_after is not None:
        return float(retry_after)
    base = min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)
    return base + base * 0.25 * random()


def with_retry(call, on_overflow=None, fallback_model=None,
               max_retries=DEFAULT_MAX_RETRIES, sleep=time.sleep):
    """Call `call()`, recovering per error class. Returns its result, or raises
    once recovery is exhausted so the loop can feed the error back (section 1)."""
    consecutive_529 = 0
    overflowed = False
    for attempt in range(1, max_retries + 2):
        try:
            return call()
        except FallbackTriggered:
            raise
        except Exception as e:
            if _is_overflow(e):
                if on_overflow is None or overflowed:
                    raise                       # already compacted once; nothing left to shrink
                overflowed = True
                on_overflow()                   # compact (section 8), then retry
                continue
            status = _status(e)
            if status is None:
                raise                           # not a recognized API error -> fatal
            if status == 529:
                consecutive_529 += 1
                if fallback_model and consecutive_529 >= MAX_529_RETRIES:
                    raise FallbackTriggered(fallback_model)
            if attempt > max_retries or not should_retry(status):
                raise                           # exhausted or non-retryable -> surface it
            sleep(retry_delay(attempt, getattr(e, "retry_after", None)))
    raise RuntimeError("with_retry exhausted")  # unreachable: the loop returns or raises first


def _status(e):
    """The HTTP status of an API error, or None. The anthropic SDK exposes
    status_code; the offline test sets the same attribute."""
    return getattr(e, "status_code", None)


def _is_overflow(e) -> bool:
    return getattr(e, "overflow", False) or "prompt is too long" in str(e).lower()
