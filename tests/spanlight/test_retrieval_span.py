from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import spanlight
from spanlight.attributes import ERROR_TYPE, RETRIEVAL_INDEX, RETRIEVAL_K


def test_records_index_and_k(spans: InMemorySpanExporter) -> None:
    with spanlight.retrieval_span("schemes-v3", k=8):
        pass

    (span,) = spans.get_finished_spans()
    assert span.attributes[RETRIEVAL_INDEX] == "schemes-v3"
    assert span.attributes[RETRIEVAL_K] == 8


def test_k_is_absent_when_not_supplied(spans: InMemorySpanExporter) -> None:
    with spanlight.retrieval_span("schemes-v3"):
        pass

    (span,) = spans.get_finished_spans()
    assert RETRIEVAL_K not in span.attributes


def test_a_failing_retrieval_records_the_error_class(spans: InMemorySpanExporter) -> None:
    class IndexUnavailable(Exception):
        pass

    with pytest.raises(IndexUnavailable), spanlight.retrieval_span("schemes-v3"):
        raise IndexUnavailable("pgvector connection refused")

    (span,) = spans.get_finished_spans()
    assert span.attributes[ERROR_TYPE] == "IndexUnavailable"
    assert span.status.status_code is StatusCode.ERROR
