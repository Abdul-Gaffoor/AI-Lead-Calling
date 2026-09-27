"""Guardrails on the deployed stack's network exposure.

Both production incidents so far came from a difference between what was
tested and what actually ran on the server. TLS is exactly that kind of
setting: nothing in the test suite notices if the API starts answering
plaintext on a public port again, and the failure is silent — the platform
keeps working while passwords and customer records cross the network in the
clear. These tests read the real deployment files.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"
COMPOSE = DEPLOY_DIR / "docker-compose.prod.yml"
WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/deploy.yml"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def _published_ports(service: dict) -> list[str]:
    return [str(entry) for entry in service.get("ports", [])]


def test_the_api_is_never_published_in_plaintext():
    """The app container must bind to loopback only.

    `"8000:8000"` publishes on every interface, which is how the API ended up
    reachable over plain HTTP. `"127.0.0.1:8000:8000"` keeps the local health
    check and `docker compose exec` admin tasks working without exposing it.
    """
    app = _compose()["services"]["app"]

    for published in _published_ports(app):
        assert published.startswith("127.0.0.1:"), (
            f"app publishes {published!r} on all interfaces — the API would "
            "answer plain HTTP from the internet. Bind it to 127.0.0.1."
        )


def test_a_tls_terminator_serves_https():
    traefik = _compose()["services"]["traefik"]
    published = _published_ports(traefik)

    assert any(p.startswith("443:") for p in published), published
    # Port 80 must stay open: it carries the HTTPS redirect and answers the
    # ACME HTTP-01 challenge that issues and renews the certificate.
    assert any(p.startswith("80:") for p in published), published


def test_certificates_survive_a_redeploy():
    """Without a persistent volume the certificate is re-issued on every
    deploy, which hits Let's Encrypt's rate limit and takes HTTPS down."""
    compose = _compose()
    mounts = compose["services"]["traefik"]["volumes"]

    assert any("letsencrypt" in str(m) for m in mounts), mounts
    assert "letsencrypt" in compose["volumes"]


def test_the_proxy_routes_to_the_api_and_forces_hsts():
    labels = _compose()["services"]["app"]["labels"]
    joined = "\n".join(labels)

    assert "traefik.enable=true" in joined
    assert "loadbalancer.server.port=8000" in joined
    assert "Host(`${DOMAIN}`)" in joined, "the router must match the configured domain"
    assert "headers.stsSeconds=31536000" in joined, "HSTS"


def test_nothing_is_exposed_without_opting_in():
    """exposedByDefault would publish every container on the box, including
    the database, the moment it joined this network."""
    command = "\n".join(_compose()["services"]["traefik"]["command"])
    assert "--providers.docker.exposedByDefault=false" in command


def test_the_traefik_dashboard_is_off():
    """It exposes the whole routing table unauthenticated."""
    command = "\n".join(_compose()["services"]["traefik"]["command"])
    assert "--api.dashboard=false" in command
    assert "--api.insecure=true" not in command


def test_the_deploy_refuses_to_run_without_a_domain():
    """Traefik routes on Host(DOMAIN). With none set the router never loads
    and the site answers nothing, so the deploy stops instead."""
    workflow = WORKFLOW.read_text()
    assert 'ERROR: DOMAIN is not set.' in workflow
    assert 'ERROR: ACME_EMAIL is not set' in workflow


def test_the_deploy_fails_when_https_does_not_answer():
    """A deploy that leaves the proxy broken is a failed deploy: customers only
    ever reach the platform through TLS."""
    workflow = WORKFLOW.read_text()

    assert 'https://${DOMAIN}/health' in workflow


def test_a_password_with_url_characters_still_connects(monkeypatch):
    """A password is arbitrary text. Pasted straight into a DSN, an "@" makes
    the rest of it look like a hostname, so the app quietly tries to reach the
    wrong server and reports only that the database "did not become
    available" — which is exactly how a deploy failed once.
    """
    from sqlalchemy.engine import make_url

    from backend.core.config import Settings

    # The test suite pins DATABASE_URL to SQLite; drop it so the parts are used.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    awkward = "p@ss:w/rd#1 ?&%"
    settings = Settings(_env_file=None, postgres_password=awkward)
    url = make_url(settings.database_url)

    assert url.host == "db", url
    assert url.database == "swaraj_solar", url
    assert url.username == "swaraj", url
    assert url.password == awkward, "the password must survive encoding intact"


def test_alembic_can_take_a_percent_encoded_url(monkeypatch):
    """Alembic stores the URL in a ConfigParser, which reads "%" as
    interpolation syntax — so percent-encoding the password made every
    migration crash at startup with "invalid interpolation syntax". env.py
    doubles the percent signs; this proves the escape actually works.
    """
    from configparser import ConfigParser

    from backend.core.config import Settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = Settings(_env_file=None, postgres_password="p@ss:w/rd#1 ?&%").database_url
    assert "%" in url, "this test is pointless unless the URL is encoded"

    parser = ConfigParser()
    parser.add_section("alembic")
    parser.set("alembic", "sqlalchemy.url", url.replace("%", "%%"))

    assert parser.get("alembic", "sqlalchemy.url") == url, (
        "the escape must round-trip to the original URL"
    )


def test_migrations_escape_the_url_for_configparser():
    env_py = Path(__file__).resolve().parents[2] / "database/migrations/env.py"
    assert 'replace("%", "%%")' in env_py.read_text()


def test_an_explicit_database_url_always_wins():
    """Tests point at SQLite this way, so the parts must never override it."""
    from backend.core.config import Settings

    settings = Settings(_env_file=None, database_url="sqlite://", postgres_password="x")
    assert settings.database_url == "sqlite://"


def test_the_compose_file_does_not_hand_craft_a_dsn():
    """The app assembles the URL so it can encode the password; a DSN built by
    string interpolation in YAML cannot."""
    raw = COMPOSE.read_text()

    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}" in raw
    assert "DATABASE_URL: postgresql" not in raw, (
        "compose is interpolating the password into a URL again"
    )


def test_the_entrypoint_reports_why_the_database_is_unreachable():
    """Without the reason, a wrong password and a slow boot look identical."""
    entrypoint = (DEPLOY_DIR / "entrypoint.sh").read_text()

    assert "last_error" in entrypoint
    assert "hide_password=True" in entrypoint, "never log the password"


def test_recordings_outlive_a_redeploy():
    """Local recording storage inside a container is wiped on every deploy. The
    audio a manager is meant to review has to be on a volume, and the worker
    needs the same one to enforce the retention period."""
    compose = _compose()

    for service in ("app", "worker"):
        mounts = [str(m) for m in compose["services"][service].get("volumes", [])]
        assert any("recordings" in m for m in mounts), (
            f"{service} has no recordings volume: {mounts}"
        )
    assert "recordings" in compose["volumes"]


def test_uvicorn_trusts_only_the_proxy_for_forwarded_headers():
    """The app must honour X-Forwarded-Proto — otherwise FastAPI answers a
    redirect with an http:// location and drops the customer out of TLS — but
    it must not trust those headers from arbitrary senders by default.
    """
    entrypoint = (DEPLOY_DIR / "entrypoint.sh").read_text()

    assert "--proxy-headers" in entrypoint
    assert '--forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"' in entrypoint


def test_the_proxy_can_talk_to_the_docker_daemon():
    """Traefik reads its routes off container labels, so the Docker provider
    starting is the difference between the site working and every request
    404ing.

    Traefik up to and including 3.5 pins Docker API version 1.24 when it
    dials the socket. Docker Engine on the deployment host rejects anything
    below 1.40, so the provider never starts, no router is built from the
    labels below, and Traefik answers 404 for everything — while the stack
    reports healthy and the deploy looks like it worked. 3.6 negotiates the
    API version with the daemon. Downgrading brings the outage back.
    """
    image = _compose()["services"]["traefik"]["image"]

    repository, _, tag = image.partition(":")
    assert repository == "traefik", image
    major, minor = (int(part) for part in tag.lstrip("v").split(".")[:2])
    assert (major, minor) >= (3, 6), (
        f"{image} pins Docker API 1.24 and cannot read container labels on "
        "this host; use traefik:v3.6 or newer"
    )
