from __future__ import annotations

from fastapi import FastAPI

import spanlight
from app.config import get_settings
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import agent, demo, health


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="spanlight", version="0.1.0")
    app.add_middleware(RequestContextMiddleware)

    app.include_router(health.router)
    app.include_router(agent.router)
    app.include_router(demo.router)

    # Replaces the chassis `otel_bootstrap.setup_otel`, which this repo deleted.
    # Two modules racing to call `set_tracer_provider` is a silent failure of
    # exactly the kind Spanlight is built to catch, so there is only one now.
    #
    # The endpoint is passed rather than left to the environment because
    # pydantic-settings reads `.env` into this object, not into `os.environ`.
    spanlight.init(
        service="spanlight-demo-agent",
        endpoint=settings.otel_exporter_otlp_endpoint,
        headers=settings.otel_exporter_otlp_headers,
    )

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
