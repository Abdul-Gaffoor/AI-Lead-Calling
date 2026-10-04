"""Metrics and tracing (MVP section 33).

Off by default, and that default is deliberate rather than lazy. The rest
of this platform runs on mocks that make no network calls and cost
nothing; an observability layer that shipped data somewhere by default
would be the one piece that did not, and it would do it with call
metadata.

Two things, separately switchable:

* **Prometheus metrics** at `/metrics`, exposed only when
  `METRICS_ENABLED` is on. No dependency: the exposition format is a few
  lines of text, and a counter that needs a library to add one to an
  integer is not worth the supply chain.
* **OpenTelemetry tracing**, which does need the SDK, and is therefore
  optional — if `OTEL_ENABLED` is on and the packages are missing, that
  is said plainly at startup rather than crashing the API or, worse,
  silently doing nothing.

What is measured is chosen to answer operational questions, not to fill a
dashboard: are calls connecting, is the AI replying quickly enough, is the
queue draining. Nothing here carries a phone number or a transcript —
metric labels end up in a time-series database with a different retention
policy and a different audience from the call records.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from backend.core.config import settings

logger = logging.getLogger(__name__)

#: Latency buckets in seconds. Chosen around what matters for a voice
#: call: under half a second feels immediate, over two seconds feels
#: broken, and the tail is where the complaints come from.
LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)


@dataclass
class _Histogram:
    buckets: tuple[float, ...] = LATENCY_BUCKETS
    counts: dict[float, int] = field(default_factory=lambda: defaultdict(int))
    total: float = 0.0
    observations: int = 0

    def observe(self, seconds: float) -> None:
        self.total += seconds
        self.observations += 1
        for bound in self.buckets:
            if seconds <= bound:
                # Only the smallest bucket that fits. `render` sums them
                # into the cumulative counts Prometheus expects; doing it
                # here as well would count every observation once per
                # bucket it falls under.
                self.counts[bound] += 1
                break


class Metrics:
    """A tiny Prometheus-compatible registry.

    Thread-safe because uvicorn serves requests from a worker pool and a
    counter incremented from two threads at once otherwise loses writes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple], float] = {}
        self._histograms: dict[tuple[str, tuple], _Histogram] = {}
        self._help: dict[str, str] = {}

    @staticmethod
    def _key(name: str, labels: dict | None) -> tuple[str, tuple]:
        return name, tuple(sorted((labels or {}).items()))

    def describe(self, name: str, help_text: str) -> None:
        self._help[name] = help_text

    def increment(self, name: str, *, labels: dict | None = None, by: float = 1.0) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += by

    def set_gauge(self, name: str, value: float, *, labels: dict | None = None) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def observe(self, name: str, seconds: float, *, labels: dict | None = None) -> None:
        with self._lock:
            key = self._key(name, labels)
            if key not in self._histograms:
                self._histograms[key] = _Histogram()
            self._histograms[key].observe(seconds)

    def reset(self) -> None:
        """For tests. A registry that leaks between them is unreadable."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    def render(self) -> str:
        """The Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            for name, help_text in sorted(self._help.items()):
                lines.append(f"# HELP {name} {help_text}")

            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"{name}{_labels(labels)} {value:g}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"{name}{_labels(labels)} {value:g}")

            for (name, labels), histogram in sorted(self._histograms.items()):
                cumulative = 0
                for bound in histogram.buckets:
                    cumulative += histogram.counts.get(bound, 0)
                    lines.append(
                        f"{name}_bucket{_labels(labels, le=str(bound))} {cumulative}"
                    )
                lines.append(
                    f"{name}_bucket{_labels(labels, le='+Inf')} {histogram.observations}"
                )
                lines.append(f"{name}_sum{_labels(labels)} {histogram.total:g}")
                lines.append(f"{name}_count{_labels(labels)} {histogram.observations}")

        return "\n".join(lines) + "\n"


def _labels(labels: tuple, **extra: str) -> str:
    pairs = list(labels) + list(extra.items())
    if not pairs:
        return ""
    rendered = ",".join(f'{key}="{_escape(str(value))}"' for key, value in pairs)
    return "{" + rendered + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


#: The process-wide registry.
metrics = Metrics()

metrics.describe("swaraj_http_requests_total", "HTTP requests by method, path and status.")
metrics.describe("swaraj_http_request_seconds", "HTTP request duration in seconds.")
metrics.describe("swaraj_calls_placed_total", "Outbound calls placed, by provider.")
metrics.describe("swaraj_call_dispositions_total", "Calls finished, by disposition.")
metrics.describe("swaraj_ai_turn_seconds", "Time to produce one AI reply.")
metrics.describe("swaraj_transfers_total", "Live transfer attempts, by outcome.")


def record_request(method: str, path: str, status: int, seconds: float) -> None:
    """One HTTP request.

    The path is the route template, never the resolved URL: labelling with
    `/calls/8213` would create a new time series per call and melt the
    metrics store, besides putting an identifier where it does not belong.
    """
    if not settings.metrics_enabled:
        return
    labels = {"method": method, "path": path, "status": str(status)}
    metrics.increment("swaraj_http_requests_total", labels=labels)
    metrics.observe(
        "swaraj_http_request_seconds", seconds, labels={"method": method, "path": path}
    )


def record_call_placed(provider: str) -> None:
    if settings.metrics_enabled:
        metrics.increment("swaraj_calls_placed_total", labels={"provider": provider})


def record_disposition(disposition: str) -> None:
    if settings.metrics_enabled:
        metrics.increment(
            "swaraj_call_dispositions_total", labels={"disposition": disposition}
        )


def record_ai_turn(seconds: float, language: str) -> None:
    if settings.metrics_enabled:
        metrics.observe("swaraj_ai_turn_seconds", seconds, labels={"language": language})


def record_transfer(outcome: str) -> None:
    if settings.metrics_enabled:
        metrics.increment("swaraj_transfers_total", labels={"outcome": outcome})


class Timer:
    """`with Timer() as t:` ... `t.seconds`."""

    def __enter__(self) -> "Timer":
        self._started = time.monotonic()
        self.seconds = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.seconds = time.monotonic() - self._started


def setup_tracing(app) -> bool:
    """Instrument the app with OpenTelemetry, if it is switched on.

    Returns whether tracing actually started. A deployment that asked for
    tracing and did not get it is told why — silently not tracing is the
    worst of the three outcomes, because the absence of spans looks like
    the absence of traffic.
    """
    if not settings.otel_enabled:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.error(
            "OTEL_ENABLED is on but the OpenTelemetry packages are not "
            "installed. Add opentelemetry-sdk, "
            "opentelemetry-exporter-otlp-proto-http and "
            "opentelemetry-instrumentation-fastapi, or set OTEL_ENABLED=false. "
            "Running WITHOUT tracing."
        )
        return False

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint or None))
    )
    trace.set_tracer_provider(provider)
    # /health is excluded: it is polled every few seconds by Docker and by
    # the deploy check, and tracing it buries real traffic.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    logger.info("OpenTelemetry tracing enabled, exporting to %s", settings.otel_endpoint)
    return True
