from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import spanlight
from app.config import get_settings
from llm import ChatClient, ChatMessage, ProviderClientError, ProviderConfigError, ProviderError
from spanlight.attributes import GEN_AI_RESPONSE_MODEL

router = APIRouter(prefix="/agent", tags=["agent"])

# Module-level singleton so retry and throttle state persist across requests
# within one process.
client = ChatClient(max_retry_attempts=get_settings().llm_max_retry_attempts)


class AgentRequest(BaseModel):
    prompt: str


class AgentReply(BaseModel):
    text: str
    provider: str
    model: str
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    latency_ms: float
    session_id: str


class SchemeIndexUnavailable(RuntimeError):
    pass


@spanlight.tool("lookup_scheme")
async def _lookup_scheme(query: str) -> str:
    """Stand-in for a real tool, with one deliberately reachable failure.

    A prompt containing `fail-tool` makes it raise. That is how the demo can
    show a silent tool failure being caught: the route swallows the error and
    answers anyway, which is exactly the behaviour the detector exists to
    notice, and a demo that cannot reach the failure proves nothing about the
    detector that watches for it.
    """
    if "fail-tool" in query:
        raise SchemeIndexUnavailable("scheme index unavailable")
    return f"scheme record for {query}"


async def _retrieve(query: str, k: int) -> list[str]:
    with spanlight.retrieval_span("demo-index", k=k):
        return [f"chunk {i} for {query}" for i in range(k)]


@router.post("/run", response_model=AgentReply)
async def run(request: AgentRequest) -> AgentReply:
    """The traffic source for the traces this project exists to show.

    Returns the session id so a visitor can find their own trace in Grafana.
    Without it the demo asks people to take on faith that anything was recorded,
    which is the posture this project argues against.

    Uses the context-manager form rather than the decorator because the provider
    is a per-request settings value, so there is nothing to name at import time.
    The HTTP status mapping happens outside the span on purpose: converting
    inside it would record `error.type=HTTPException` and lose the provider
    error class that actually explains the failure.
    """
    settings = get_settings()

    with spanlight.session() as session_id:
        # Swallowed on purpose, and this is the bug the demo is showing. The
        # tool span records the error, the run carries on to the model, and the
        # reply comes back looking healthy. Nothing here reports the failure,
        # which is why the detector has to.
        with suppress(SchemeIndexUnavailable):
            await _lookup_scheme(request.prompt)
        await _retrieve(request.prompt, k=3)

        try:
            with spanlight.model_span(provider=settings.llm_provider) as span:
                response = await client.complete(
                    settings.llm_provider,
                    [ChatMessage(role="user", content=request.prompt)],
                )
                span.set_attribute(GEN_AI_RESPONSE_MODEL, response.model)
                spanlight.record_usage(
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                    cost_usd=response.cost_usd,
                    provider=response.provider,
                )
        except ProviderConfigError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except ProviderClientError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AgentReply(**response.model_dump(), session_id=session_id)
