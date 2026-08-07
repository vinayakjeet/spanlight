from __future__ import annotations

from collections.abc import Mapping

from opentelemetry import baggage
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject

from spanlight._session import current_session_id
from spanlight.attributes import SESSION_ID


def headers(carrier: dict[str, str] | None = None) -> dict[str, str]:
    """Headers that let the far side of a call join this trace.

        reply = await client.post(url, headers=spanlight.headers())

    Carries the session id in W3C baggage rather than a header of our own.
    Baggage is the mechanism every OTel SDK already reads, so a service
    instrumented with plain OpenTelemetry and no Spanlight still forwards it to
    the next hop untouched. A custom header would be dropped at the first such
    service, and the break would show up as two unrelated sessions rather than
    as an error.

    The key is the same `spanlight.session.id` used for the span attribute. One
    name for one concept, so a query does not depend on which surface it came
    from.
    """
    carrier = {} if carrier is None else carrier
    session_id = current_session_id()
    context = baggage.set_baggage(SESSION_ID, session_id) if session_id else None
    inject(carrier, context=context)
    return carrier


def remote_context(inbound: Mapping[str, str]) -> Context:
    """Rebuild the caller's context from inbound request headers."""
    return extract(dict(inbound))


def remote_session_id(context: Context) -> str | None:
    value = baggage.get_baggage(SESSION_ID, context)
    return str(value) if value is not None else None
