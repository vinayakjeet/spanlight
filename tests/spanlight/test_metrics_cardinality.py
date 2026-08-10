from __future__ import annotations

import pathlib

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import spanlight
import spanlight._metrics as metrics_module
from spanlight._detector_framework import registry
from spanlight._detectors import loop_detector
from spanlight._export import CountedExporter
from spanlight.attributes import (
    DETECTIONS_TOTAL,
    EXPORT_FAILURES_TOTAL,
    METRIC_LABELS,
    SESSION_COST_USD,
    SESSION_ID,
    TOKEN_USAGE,
)

SPEC = pathlib.Path(__file__).resolve().parents[2] / "SPEC.md"
METRICS_HEADER = "| Metric | Type | Labels |"

IMPLEMENTED = {DETECTIONS_TOTAL, EXPORT_FAILURES_TOTAL, SESSION_COST_USD, TOKEN_USAGE}


def _spec_metric_labels() -> dict[str, frozenset[str]]:
    lines = SPEC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(METRICS_HEADER))

    declared = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.split("|")[1:-1]]
        name = cells[0].strip("`")
        labels = {label.strip().strip("`") for label in cells[2].split(",")}
        declared[name] = frozenset(labels)
    return declared


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    """One meter for both counters, so a single sweep sees everything emitted."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    monkeypatch.setattr(
        metrics_module, "_counter", lambda: meter.create_counter(DETECTIONS_TOTAL)
    )
    monkeypatch.setattr(
        metrics_module,
        "_export_failures",
        lambda: meter.create_counter(EXPORT_FAILURES_TOTAL),
    )
    monkeypatch.setattr(
        metrics_module,
        "_session_cost",
        lambda: meter.create_histogram(SESSION_COST_USD),
    )
    monkeypatch.setattr(
        metrics_module, "_token_usage", lambda: meter.create_histogram(TOKEN_USAGE)
    )
    return reader


def _emitted(reader: InMemoryMetricReader) -> dict[str, set[str]]:
    labels: dict[str, set[str]] = {}
    data = reader.get_metrics_data()
    assert data is not None, "nothing was recorded, so this proved nothing"
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    labels.setdefault(metric.name, set()).update(point.attributes)
    return labels


def _emit_everything(spans, reader: InMemoryMetricReader) -> dict[str, set[str]]:  # noqa: ARG001
    """Drive both counters through their real call paths."""
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class Rejects(SpanExporter):
        def export(self, spans) -> SpanExportResult:  # noqa: ARG002
            return SpanExportResult.FAILURE

        def shutdown(self) -> None:
            pass

    registry.register(loop_detector)
    with spanlight.session():
        for _ in range(3):
            with spanlight.tool_span("search", args={"q": "pm-kisan"}):
                pass
        # Priced, or `cost_usd_equivalent` is None and the session histogram
        # records nothing, which would leave this sweep checking three metrics
        # while claiming four.
        with spanlight.model_span(provider="groq"):
            spanlight.record_usage(
                tokens_in=412, tokens_out=88, cost_usd=0.0, provider="groq"
            )

    CountedExporter(Rejects()).export([])
    return _emitted(reader)


def test_no_metric_carries_a_label_outside_the_allowlist(
    spans, recorded: InMemoryMetricReader
) -> None:
    for name, labels in _emit_everything(spans, recorded).items():
        allowed = METRIC_LABELS[name]
        assert labels <= allowed, f"{name} emitted {sorted(labels - allowed)}"


def test_session_id_is_never_a_metric_label(
    spans, recorded: InMemoryMetricReader
) -> None:
    """The specific mistake worth naming. Session id is on every span, it reads
    like a useful grouping, and as a label it creates one time series per run,
    which exhausts the free tier's active-series limit in days."""
    for name, labels in _emit_everything(spans, recorded).items():
        assert SESSION_ID not in labels, name
        assert not any("session" in label for label in labels), f"{name}: {labels}"


def test_the_allowlist_matches_the_spec_table() -> None:
    """Both directions, like the attribute contract. A label documented but not
    allowed would be queried and never appear; one allowed but not documented
    would show up in a bill nobody predicted."""
    assert _spec_metric_labels() == METRIC_LABELS


def test_the_spec_table_was_actually_found() -> None:
    """Guards the test above, which compares against whatever the parser returns.
    If the table moved, the parser would yield nothing and the comparison would
    quietly pass while checking nothing."""
    assert len(_spec_metric_labels()) >= 4


def test_every_implemented_metric_is_actually_emitted(
    spans, recorded: InMemoryMetricReader
) -> None:
    """Otherwise the allowlist above is checked against an empty set and every
    assertion in this file is vacuously true."""
    assert set(_emit_everything(spans, recorded)) == IMPLEMENTED
