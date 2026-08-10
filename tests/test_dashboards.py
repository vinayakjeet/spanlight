from __future__ import annotations

import json
import pathlib
import re

import pytest

from spanlight.attributes import CONTRACT, METRIC_LABELS

DASHBOARDS = pathlib.Path(__file__).resolve().parents[1] / "dashboards"

# `spanlight_session_cost_usd_sum` is the histogram `spanlight_session_cost_usd`
# once Prometheus has split it into its parts.
HISTOGRAM_SUFFIXES = ("_sum", "_count", "_bucket")

METRIC_REFERENCE = re.compile(r"\b(spanlight_[a-z_]+|gen_ai_[a-z_]+)\b")
SPAN_ATTRIBUTE_REFERENCE = re.compile(r"span\.([a-z_][a-z_.]*)")
GROUPING = re.compile(r"(?:sum|avg|min|max) by \(([^)]*)\)")


def files() -> list[pathlib.Path]:
    return sorted(DASHBOARDS.glob("*.json"))


def queries(document: object) -> list[str]:
    """Every query string anywhere in a dashboard, however deeply nested."""
    found: list[str] = []
    if isinstance(document, dict):
        for key, value in document.items():
            if key in {"expr", "query"} and isinstance(value, str):
                found.append(value)
            else:
                found.extend(queries(value))
    elif isinstance(document, list):
        for item in document:
            found.extend(queries(item))
    return found


def metrics_in(query: str) -> set[str]:
    """Metric names, with grouping labels excluded.

    `sum by (type, gen_ai_system)` puts a label in a position that looks exactly
    like a metric name to a regex, and reading it as one turns a correct query
    into a failure about a metric nobody emits.
    """
    grouped = {
        label.strip()
        for clause in GROUPING.findall(query)
        for label in clause.split(",")
    }
    return {
        base_metric(name)
        for name in METRIC_REFERENCE.findall(query)
        if name not in grouped
    }


def base_metric(name: str) -> str:
    for suffix in HISTOGRAM_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_the_file_is_valid_json(path: pathlib.Path) -> None:
    json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_every_metric_queried_is_one_spanlight_emits(path: pathlib.Path) -> None:
    """The failure this prevents is silent. A renamed metric leaves a dashboard
    that loads, renders its panels, and draws nothing, and the reader concludes
    the system is quiet rather than that the query is wrong."""
    document = json.loads(path.read_text(encoding="utf-8"))

    for query in queries(document):
        for reference in metrics_in(query):
            assert reference in METRIC_LABELS, (
                f"{path.name} queries {reference}, which Spanlight does not emit"
            )


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_every_span_attribute_queried_is_in_the_contract(path: pathlib.Path) -> None:
    """Same failure, on the trace side. TraceQL returns an empty result for an
    attribute that does not exist rather than an error."""
    document = json.loads(path.read_text(encoding="utf-8"))

    for query in queries(document):
        for reference in SPAN_ATTRIBUTE_REFERENCE.findall(query):
            assert reference.rstrip(".") in CONTRACT, (
                f"{path.name} queries span.{reference}, which is not in the contract"
            )


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name)
def test_no_query_groups_by_a_label_the_metric_cannot_carry(
    path: pathlib.Path,
) -> None:
    """A `by (session_id)` would return nothing, because the label is not there.
    It is not there because as a metric label it creates one time series per run
    and exhausts the free tier's active-series limit within days."""
    document = json.loads(path.read_text(encoding="utf-8"))

    for query in queries(document):
        metrics = metrics_in(query)
        allowed = set().union(*(METRIC_LABELS[m] for m in metrics)) if metrics else set()
        # Compared with dots flattened to underscores. An attribute named
        # `gen_ai.system` in the contract arrives in Prometheus as
        # `gen_ai_system`, because a dot is not legal in a label name, and a
        # query written the way the contract spells it returns nothing at all.
        allowed = {label.replace(".", "_") for label in allowed}
        for clause in GROUPING.findall(query):
            for label in (label.strip() for label in clause.split(",")):
                assert label.replace(".", "_") in allowed, (
                    f"{path.name} groups by {label!r}"
                )


def test_the_alert_watches_an_increase_rather_than_the_counter() -> None:
    """A counter only rises, so a rule on its value fires forever after the first
    detection and gets muted inside a day. The question worth asking is whether
    anything fired recently."""
    alert = json.loads((DASHBOARDS / "alert_detections.json").read_text(encoding="utf-8"))
    expression = alert["data"][0]["model"]["expr"]

    assert "increase(" in expression
    assert "spanlight_detections_total" in expression


def test_the_alert_does_not_wait_for_a_detection_to_persist() -> None:
    """A detection is an event, not a level. Requiring it to be sustained means a
    single silent failure never alerts, which is the one this exists to catch."""
    alert = json.loads((DASHBOARDS / "alert_detections.json").read_text(encoding="utf-8"))

    assert alert["for"] == "0s"


def test_no_data_is_not_an_alert() -> None:
    """A counter that has never incremented publishes no series at all. Paging on
    that pages every healthy service that has never failed."""
    alert = json.loads((DASHBOARDS / "alert_detections.json").read_text(encoding="utf-8"))

    assert alert["noDataState"] == "OK"


def test_the_checks_above_would_catch_a_bad_dashboard() -> None:
    """The parametrized tests only prove the current files are clean. If the
    extraction were broken they would pass over anything, which is how a
    contract test becomes decoration."""
    renamed = "sum by (type) (increase(spanlight_detection_total[5m]))"
    session_label = "sum by (session_id) (increase(spanlight_detections_total[5m]))"
    unknown_attribute = '{ span.spanlight.prompt.text != nil }'

    assert base_metric("spanlight_detection_total") not in METRIC_LABELS
    assert METRIC_REFERENCE.findall(renamed) == ["spanlight_detection_total"]
    assert GROUPING.findall(session_label) == ["session_id"]
    assert "session_id" not in METRIC_LABELS["spanlight_detections_total"]
    assert SPAN_ATTRIBUTE_REFERENCE.findall(unknown_attribute) == ["spanlight.prompt.text"]
    assert "spanlight.prompt.text" not in CONTRACT


def test_the_query_extractor_reaches_nested_panels() -> None:
    """Grafana nests targets inside panels inside rows. An extractor that only
    looked one level down would find nothing and every check above would pass."""
    found = queries(json.loads((DASHBOARDS / "fleet.json").read_text(encoding="utf-8")))

    assert len(found) > 5


def test_both_dashboards_are_present() -> None:
    """Guards the parametrized tests above, which pass vacuously over an empty
    directory."""
    assert {path.name for path in files()} >= {"fleet.json", "session.json"}
