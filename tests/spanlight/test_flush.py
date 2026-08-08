from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from tests.spanlight.collector import collector

REPO_ROOT = Path(__file__).resolve().parents[2]

PROBE = """
import spanlight
spanlight.init("test-flush", endpoint="{endpoint}", sample_rate=1.0)

with spanlight.model_span(provider="mock"):
    pass
"""


def test_spans_are_flushed_on_process_exit() -> None:
    """A process that exits immediately after a span still exports it.

    `BatchSpanProcessor` exports on a timer, so a short-lived run finishes long
    before the first tick and its spans die with it. That is the common case
    rather than an edge one: a CLI invocation, a CI gate job, a cron task. The
    trace of a failing gate is exactly the one worth keeping, and it is the one
    most likely to be lost, because the process ends as soon as it fails.

    The probe does not flush, and neither does Spanlight. Both SDK providers
    default to `shutdown_on_exit=True` and shutdown flushes, so this pins someone
    else's guarantee rather than our own code, which is exactly why it is worth
    pinning: if it stops holding, every short run silently stops reporting and
    nothing points at the cause.

    Spanlight did register its own `atexit` flush until a mutation test deleted
    it and this test stayed green, which is how the duplication was found.
    """
    with collector() as (server, endpoint):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(PROBE.format(endpoint=endpoint))],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr

        time.sleep(0.5)
        posts = list(server.paths)

    assert posts == ["/v1/traces"]
