from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

# A ContextVar rather than a module global, because an agent process runs many
# sessions concurrently under asyncio and a global would hand every span the id
# of whichever session started most recently.
_current_session: ContextVar[str | None] = ContextVar("spanlight_session_id", default=None)


def current_session_id() -> str | None:
    return _current_session.get()


@contextmanager
def bind(session_id: str) -> Iterator[str]:
    """Make `session_id` the current session for the duration of the block.

    Only the context variable. The session *span* is assembled in `_spans`,
    which needs this binding to already be in place so the span it opens is
    stamped with the id like every other span.

    Restores the previous value on exit rather than clearing it, so a nested
    session returns control to its parent instead of silently orphaning the
    remaining spans.
    """
    token = _current_session.set(session_id)
    try:
        yield session_id
    finally:
        _current_session.reset(token)
