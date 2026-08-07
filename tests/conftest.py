from __future__ import annotations

import os

# A test run must never ship spans to the real backend. It burns free-tier
# ingest, and worse, it pollutes the M7 study corpus with traffic that was never
# a real agent run, which would quietly corrupt the one artifact in this project
# that nobody else has.
#
# Set here rather than in a fixture because `app/main.py` builds the app at
# import time, so the first `from app.main import app` would already have
# exported. Environment variables outrank `.env` in pydantic-settings, so an
# empty value wins over a configured endpoint.
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = ""
