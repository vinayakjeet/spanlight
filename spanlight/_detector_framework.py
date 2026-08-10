from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.trace import Span

from spanlight._metrics import count_detection
from spanlight._session import current_session_id
from spanlight.attributes import (
    COST_USD_EQUIVALENT,
    DETECTION,
    DETECTION_EVENT,
    DETECTION_TYPE,
)

# Where a session's running cost lives. One key, written in one place, read by
# the cost detector and by the session-cost histogram. Two independent sums of
# the same spans would be a second source of truth, and the one that drifted
# would be whichever nobody had a test for.
COST_TOTAL = "cost_usd_equivalent"


@dataclass(frozen=True)
class Detection:
    """What a detector returns when it fires.

    `details` becomes the event payload. It exists because the attribute alone
    says a run tripped something without saying what, which leaves whoever opens
    the trace re-deriving the arithmetic the detector already did.
    """

    type: str
    details: Mapping[str, Any] = field(default_factory=dict)


Detector = Callable[[dict, Span], Detection | None]

SPAN = "span"
SESSION = "session"


class DetectorRegistry:
    """Runs detectors while the span they will mark is still open.

    Deliberately not a `SpanProcessor`. A processor's `on_end` receives a
    `ReadableSpan`, which has no `set_attribute` at all, so the SPEC requirement
    that a detection lands on the offending span is unreachable from there. It
    does not degrade quietly either: it raises `AttributeError` and takes the
    span with it.

    Two phases, because the detectors ask two different kinds of question.
    A `SPAN` detector reasons about a step in isolation or about a running
    total, so it can fire the moment a threshold is crossed and mark the step
    that crossed it. A `SESSION` detector cannot answer until the run is over:
    SPEC S4 defines a silent tool failure partly by the session's *final*
    status, which is unknowable while the session is still producing spans.
    Those fire against the session span instead, which is still open at that
    point precisely because it encloses everything else.
    """

    def __init__(
        self,
        max_sessions: int = 1024,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self.detectors: dict[str, list[Detector]] = {SPAN: [], SESSION: []}
        # Injectable so the eviction bounds can be tested exactly instead of by
        # sleeping, which would trade a slow suite for a flaky one.
        self._clock = clock
        self._state: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def register(self, detector: Detector, phase: str = SPAN) -> None:
        self.detectors[phase].append(detector)

    def state_for(self, session_id: str) -> dict:
        """Session-scoped scratch space, bounded two ways.

        `release` returns a session's entry the moment it ends, so in the
        healthy case this map holds only sessions currently in flight. The LRU
        cap and the TTL exist for the unhealthy case: a process killed mid
        session, or a `session()` whose context was never exited, would
        otherwise leak an entry per run forever. A library that claims a memory
        bound cannot keep an unbounded dict at the centre of it.

        The TTL runs from last use, not from creation. Measured from creation it
        would evict a session that is merely long, taking its loop counters with
        it, so a slow agent would quietly stop being watched at the point it
        became most worth watching.
        """
        now = self._clock()
        entry = self._state.get(session_id)
        entry = (now, entry[1] if entry is not None else {})
        self._state[session_id] = entry
        self._state.move_to_end(session_id)
        self._evict(keep=session_id)
        return entry[1]

    def release(self, session_id: str) -> dict:
        """Drop a finished session's scratch space and hand it back.

        Returned rather than discarded so the caller can read the session's
        totals on the way out. Reading them through `state_for` instead would
        recreate the entry that was just released.
        """
        _, state = self._state.pop(session_id, (0.0, {}))
        return state

    def _evict(self, keep: str | None = None) -> None:
        """`keep` is never evicted. Without it a TTL short enough to matter can
        drop the entry created microseconds earlier by the caller, which then
        reads back scratch space that no longer exists."""
        cutoff = self._clock() - self.ttl_seconds
        for session_id in [
            s for s, (touched, _) in self._state.items() if touched < cutoff
        ]:
            if session_id != keep:
                del self._state[session_id]
        while len(self._state) > self.max_sessions:
            oldest = next(iter(self._state))
            if oldest == keep:
                break
            del self._state[oldest]

    def run(self, span: Span, phase: str = SPAN) -> None:
        # A sampled-out session hands back `NonRecordingSpan`, which has no
        # `attributes` at all, so every detector that reads them raises inside
        # the `finally` that calls this and takes the host's request with it.
        # Any host running below `sample_rate=1.0` crashed on its first dropped
        # session, and the sampler tests missed it because they register no
        # detectors while the detector tests never sample.
        #
        # Skipped rather than made defensive, because nothing a detector does to
        # a non-recording span survives: the attribute and the event both go
        # nowhere. The consequence is that detections inside dropped sessions are
        # not counted either, so `spanlight_detections_total` is sampled at the
        # same rate as the traces. At the default of 1.0 that is no consequence
        # at all, and below it the alert in M6.6 sees a fraction of what happens.
        session_id = current_session_id()
        if session_id is None or not span.is_recording():
            return

        # Accumulated before the early return below, because the session-cost
        # histogram is not a detector and must not depend on one being
        # registered. The cost ceiling has no default, so the common deployment
        # has no cost detector at all and would otherwise report every session as
        # free.
        state = self.state_for(session_id)
        equivalent = (span.attributes or {}).get(COST_USD_EQUIVALENT)
        if equivalent:
            state[COST_TOTAL] = state.get(COST_TOTAL, 0.0) + equivalent

        detectors = self.detectors[phase]
        if not detectors:
            return

        fired = state.setdefault("fired", set())
        for detector in detectors:
            detection = detector(state, span)
            if detection is None:
                continue
            # A cost ceiling stays crossed for the rest of the run, so a detector
            # that reported it on every later span would turn one problem into a
            # count of how many steps happened to follow it, and make
            # `spanlight_detections_total` a measure of session length.
            if detection.type in fired:
                continue
            fired.add(detection.type)
            self._emit(span, detection)
            return

    def _emit(self, span: Span, detection: Detection) -> None:
        """The three ways a detection surfaces, from SPEC S4 through S6.

        The attribute is what a dashboard groups by, the event is what a human
        reads once the dashboard has pointed them at a trace, and the counter is
        what an alert watches, because alerting on the presence of a span means
        querying traces on a schedule.
        """
        span.set_attribute(DETECTION, detection.type)
        span.add_event(
            DETECTION_EVENT, {DETECTION_TYPE: detection.type, **detection.details}
        )
        count_detection(detection.type)

    def reset(self) -> None:
        self._state.clear()

    def clear_detectors(self) -> None:
        self.detectors = {SPAN: [], SESSION: []}


registry = DetectorRegistry()
