# Deployment

Pushes to `main` (and the current development branch) run `.github/workflows/deploy.yml`, which:

1. **Tests** — runs the full pytest suite; nothing deploys if tests fail.
2. **Builds** — builds the backend Docker image (migrations + API, see `Dockerfile`) and pushes it to GitHub Container Registry as `ghcr.io/<owner>/<repo>:<commit-sha>`.
3. **Deploys** — copies `deploy/docker-compose.prod.yml` to `/opt/swaraj-solar` on the server over SSH, writes the `.env` file from secrets, pulls the new image, restarts the stack, and waits for `/health` to pass. On container start the app waits for Postgres, applies Alembic migrations, then serves the API on port 8000.

The workflow can also be run manually from the Actions tab (workflow_dispatch). Pull requests only run the test job.

## Required GitHub Actions configuration

The deploy job targets the GitHub environment named **`Test`** (Settings → Environments → Test). Each value can be defined there either as an **environment secret** or an **environment variable** — the workflow checks secrets first, then variables (`secrets.X || vars.X`). Repository-level secrets also work.

| Name | Purpose |
|------|---------|
| `SERVER_HOST` | Server hostname or IP |
| `SERVER_USER` | SSH user (must be able to run `docker`) |
| `SERVER_SSH_KEY` | Private SSH key for that user (the full key file contents) |
| `SERVER_PORT` | SSH port — optional, defaults to 22 |
| `JWT_SECRET` | Long random string used to sign auth tokens (e.g. `openssl rand -hex 32`) |
| `POSTGRES_PASSWORD` | Password for the production Postgres database |

> ⚠️ Prefer **secrets** for `SERVER_SSH_KEY`, `JWT_SECRET` and `POSTGRES_PASSWORD`: environment *variables* display their values in plain text to anyone with access to repo settings and are not masked in workflow logs; secrets are encrypted and masked.

`GITHUB_TOKEN` is provided automatically by Actions and is used both to push the image to GHCR and to pull it on the server during the deploy — no extra registry secret is needed. You can also add required reviewers on the `Test` environment to gate deploys.

### Telephony configuration (optional until a business number is provisioned)

The deployed stack defaults to `TELEPHONY_PROVIDER=mock`, which places **no real calls** — campaigns run end to end against a stub so the platform can be exercised safely. When the cloud-telephony account, KYC and virtual number are ready, add these and redeploy:

| Name | Kind | Purpose |
|------|------|---------|
| `TELEPHONY_PROVIDER` | variable | Set to `exotel` to dial real customers |
| `PUBLIC_BASE_URL` | variable | Public HTTPS URL of this API, so the provider can reach the status webhook |
| `TELEPHONY_WEBHOOK_TOKEN` | secret | Shared token required on `/calls/webhooks/*` |
| `EXOTEL_SID` / `EXOTEL_API_KEY` / `EXOTEL_API_TOKEN` | secrets | Exotel account credentials |
| `EXOTEL_CALLER_ID` | variable | The approved business number to show as caller ID |
| `EXOTEL_SUBDOMAIN` | variable | Defaults to `api.exotel.com` |
| `EXOTEL_FLOW_APP_ID` | variable | Call flow/applet that connects the answered call to the AI agent |

Before switching to `exotel`, confirm the exact API endpoints, request fields and callback field names against the account's own documentation — `backend/telephony/exotel.py` follows the published shape but the contract varies by provisioned product. Also confirm the caller-ID/number series against current TRAI requirements for commercial calling.

## Server prerequisites (one-time)

- Docker Engine with the Compose plugin installed (`docker compose version` works).
- The SSH user can run Docker (member of the `docker` group, or root).
- The SSH user can write to `/opt/swaraj-solar` (`sudo mkdir -p /opt/swaraj-solar && sudo chown <user> /opt/swaraj-solar`).
- Port 8000 reachable (or put nginx/Caddy in front for TLS — recommended before real use; the MVP security checklist requires TLS in production).

## First deployment

After the first successful run, create the initial super-admin on the server:

```bash
ssh <user>@<server>
cd /opt/swaraj-solar
docker compose -f docker-compose.prod.yml exec app \
  python -m backend.cli create-admin \
  --email admin@swarajsolar.com --password '<strong password>' --name "Platform Admin"
```

Then verify: `curl http://<server>:8000/health` and open `http://<server>:8000/docs`.

## Operations

```bash
cd /opt/swaraj-solar

# Status / logs
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f app

# Restart
docker compose -f docker-compose.prod.yml restart app

# Roll back: point APP_IMAGE in .env at a previous commit SHA tag, then
docker compose -f docker-compose.prod.yml up -d
```

Postgres data lives in the `pgdata` Docker volume and survives redeploys. Database backups (per the MVP security checklist) should be scheduled on the server, e.g. a cron job running `pg_dump` inside the `db` container.
