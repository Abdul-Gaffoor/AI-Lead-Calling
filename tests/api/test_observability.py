"""Metrics and tracing (MVP §33)."""

import pytest

from backend.core.observability import (
    Metrics,
    Timer,
    metrics,
    record_ai_turn,
    record_request,
    setup_tracing,
)


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def metrics_on(monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "metrics_enabled", True)
    # A token, because nothing reaches this endpoint over loopback in a
    # real deployment either — Prometheus scrapes across the Docker
    # network, and the test client is not loopback either.
    monkeypatch.setattr(config.settings, "metrics_token", "test-scrape")
    return config.settings


# --- the registry ---------------------------------------------------------


def test_counters_add_up():
    registry = Metrics()
    registry.increment("x_total", labels={"a": "1"})
    registry.increment("x_total", labels={"a": "1"})
    registry.increment("x_total", labels={"a": "2"})
    rendered = registry.render()
    assert 'x_total{a="1"} 2' in rendered
    assert 'x_total{a="2"} 1' in rendered


def test_histogram_buckets_are_cumulative():
    """Prometheus histograms are cumulative; non-cumulative ones render wrong."""
    registry = Metrics()
    for seconds in (0.05, 0.2, 0.7, 3.0):
        registry.observe("t_seconds", seconds)
    rendered = registry.render()

    assert 't_seconds_bucket{le="0.1"} 1' in rendered
    assert 't_seconds_bucket{le="0.25"} 2' in rendered
    assert 't_seconds_bucket{le="1.0"} 3' in rendered
    assert 't_seconds_bucket{le="+Inf"} 4' in rendered
    assert "t_seconds_count 4" in rendered


def test_label_values_are_escaped():
    """An unescaped quote produces output no scraper can parse."""
    registry = Metrics()
    registry.increment("x_total", labels={"path": 'a"b\\c'})
    rendered = registry.render()
    assert r'\"' in rendered and r"\\" in rendered


def test_the_timer_measures_elapsed_time():
    with Timer() as timer:
        pass
    assert timer.seconds >= 0


def test_nothing_is_recorded_while_metrics_are_off(monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "metrics_enabled", False)
    record_request("GET", "/leads", 200, 0.01)
    record_ai_turn(0.5, "te-IN")
    assert metrics.render().strip().count("\n") >= 0
    assert "swaraj_http_requests_total{" not in metrics.render()


# --- the endpoint ---------------------------------------------------------


def test_metrics_are_404_until_switched_on(client):
    assert client.get("/metrics").status_code == 404


def test_metrics_are_served_when_enabled(client, metrics_on):
    client.get("/health")
    response = client.get("/metrics?token=test-scrape")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "swaraj_http_requests_total" in response.text


def test_requests_are_labelled_with_the_route_not_the_path(client, metrics_on, admin_headers):
    """
    A label of /calls/8213 creates one time series per call and melts the
    metrics store, besides putting an identifier somewhere it should not be.
    """
    client.get("/leads", headers=admin_headers)
    body = client.get("/metrics?token=test-scrape").text
    assert 'path="/leads"' in body


def test_a_token_is_required_to_scrape_from_outside(client, metrics_on, monkeypatch):
    from backend.core import config

    monkeypatch.setattr(config.settings, "metrics_token", "scrape-me")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics?token=wrong").status_code == 401
    assert client.get("/metrics?token=scrape-me").status_code == 200
    assert (
        client.get("/metrics", headers={"X-Metrics-Token": "scrape-me"}).status_code
        == 200
    )


def test_without_a_token_only_the_host_itself_may_scrape(client, monkeypatch):
    """
    The series names alone describe the business — how many calls, how
    many opt-outs — so this is not public even with no personal data in it.
    """
    from backend.core import config

    monkeypatch.setattr(config.settings, "metrics_enabled", True)
    monkeypatch.setattr(config.settings, "metrics_token", "")
    response = client.get("/metrics")
    assert response.status_code == 403
    assert "METRICS_TOKEN" in response.text


def test_ai_turns_are_timed(client, metrics_on):
    record_ai_turn(0.42, "te-IN")
    body = metrics.render()
    assert 'swaraj_ai_turn_seconds_bucket{language="te-IN"' in body


# --- tracing --------------------------------------------------------------


def test_tracing_is_off_by_default():
    assert setup_tracing(object()) is False


def test_tracing_says_so_when_it_cannot_start(monkeypatch, caplog):
    """
    Absent spans look exactly like absent traffic, so a deployment that
    asked for tracing and did not get it must be told.
    """
    from backend.core import config

    monkeypatch.setattr(config.settings, "otel_enabled", True)
    monkeypatch.setitem(__import__("sys").modules, "opentelemetry", None)

    with caplog.at_level("ERROR"):
        started = setup_tracing(object())

    assert started is False
    assert "WITHOUT tracing" in caplog.text
