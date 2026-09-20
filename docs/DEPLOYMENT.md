# Deployment

Pushes to `master` run `.github/workflows/deploy.yml`, which:

1. **Tests** — runs the full pytest suite; nothing deploys if tests fail.
2. **Builds** — builds the backend Docker image (migrations + API, see `Dockerfile`) and pushes it to GitHub Container Registry as `ghcr.io/<owner>/<repo>:<commit-sha>`.
3. **Deploys** — copies `deploy/docker-compose.prod.yml` and `deploy/Caddyfile` to the deploy directory on the server over SSH, writes the `.env` file from secrets, pulls the new image, restarts the stack, and waits for `/health` to pass **over HTTPS**. On container start the app waits for Postgres, applies Alembic migrations, then serves the API on port 8000 — bound to loopback, because everything from outside the server arrives through the TLS terminator.

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
| `DOMAIN` | Domain pointed at the server, e.g. `swaraj.example.com` — optional, see TLS below |
| `ACME_EMAIL` | Contact address for the Let's Encrypt account — optional, used with `DOMAIN` |
| `DEPLOY_DIR` | Where the stack lives on the server — optional, defaults to `/opt/swaraj-solar` |

> ⚠️ Prefer **secrets** for `SERVER_SSH_KEY`, `JWT_SECRET` and `POSTGRES_PASSWORD`: environment *variables* display their values in plain text to anyone with access to repo settings and are not masked in workflow logs; secrets are encrypted and masked.

### TLS / HTTPS

The stack terminates TLS in a Caddy container (`deploy/Caddyfile`); the API
publishes port 8000 on loopback only, so nothing answers plain HTTP from the
internet. Caddy redirects port 80 to HTTPS and renews certificates by itself.

There are two modes, chosen by whether `DOMAIN` is set:

| `DOMAIN` | Certificate | Browser behaviour |
|---|---|---|
| set (with `ACME_EMAIL`) | Let's Encrypt, publicly trusted, auto-renewed | No warning. **Use this for the pilot.** |
| unset | Caddy's internal CA, for the server's own host/IP | Encrypted, but a warning users must click through, and HSTS is ignored |

Point the domain's A record at the server **before** the first deploy with
`DOMAIN` set: Let's Encrypt validates over port 80, and repeated failures hit a
rate limit that leaves HTTPS unavailable for hours.

The certificates live in the `caddydata` Docker volume. Keep it across
redeploys — deleting it forces re-issuance and risks that same rate limit.

Set `PUBLIC_BASE_URL` to the `https://` URL too, so telephony callbacks arrive
encrypted; with `DOMAIN` set and `PUBLIC_BASE_URL` empty, the deploy derives it.

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

### Rotating `POSTGRES_PASSWORD`

PostgreSQL reads `POSTGRES_PASSWORD` **only when it initialises an empty data
directory**. Once the `pgdata` volume exists it keeps the password it was built
with, so changing the secret alone leaves every connection failing with
`password authentication failed`. The deploy checks for this and stops with the
remedy, but in short — to keep the data:

```bash
docker compose -f docker-compose.prod.yml exec db \
  psql -U swaraj -d swaraj_solar -c "ALTER USER swaraj PASSWORD '<the new secret>'"
```

or, if nothing in the database is worth keeping yet, discard it and let the next
deploy rebuild it: `docker compose -f docker-compose.prod.yml down -v`.

## Server prerequisites (one-time)

- Docker Engine with the Compose plugin installed (`docker compose version` works).
- The SSH user can run Docker (member of the `docker` group, or root).
- The SSH user can write to the deploy directory. Two ways:
  - **Unprivileged (no root needed).** Set the `DEPLOY_DIR` Actions *variable* to a
    path inside the user's home, e.g. `/home/deploy/swaraj-solar`. The deploy
    creates it; the SSH user never needs `sudo`.
  - **Default.** Leave `DEPLOY_DIR` unset and create `/opt/swaraj-solar` as root
    once: `sudo mkdir -p /opt/swaraj-solar && sudo chown <user> /opt/swaraj-solar`.
- Ports **80 and 443** reachable from the internet (80 for the HTTPS redirect and
  certificate renewal, 443 for traffic). Port 8000 should **not** be open — the
  API is only published on loopback.

## First deployment

After the first successful run, create the initial super-admin on the server:

```bash
ssh <user>@<server>
cd <deploy directory>   # /opt/swaraj-solar, or your DEPLOY_DIR
docker compose -f docker-compose.prod.yml exec app \
  python -m backend.cli create-admin \
  --email admin@swarajsolar.com --password '<strong password>' --name "Platform Admin"
```

Then verify over TLS — `-k` is only needed while running on the internal
certificate (no `DOMAIN` set):

```bash
curl -k https://<domain-or-server>/health
```

and open `https://<domain-or-server>/` for the console, `/docs` for the API.

## Operations

```bash
cd <deploy directory>   # /opt/swaraj-solar, or your DEPLOY_DIR

# Status / logs
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f app

# Certificate problems (issuance, renewal, ACME challenges) show up here
docker compose -f docker-compose.prod.yml logs -f caddy

# Restart
docker compose -f docker-compose.prod.yml restart app

# Roll back: point APP_IMAGE in .env at a previous commit SHA tag, then
docker compose -f docker-compose.prod.yml up -d
```

Postgres data lives in the `pgdata` Docker volume and survives redeploys. Database backups (per the MVP security checklist) should be scheduled on the server, e.g. a cron job running `pg_dump` inside the `db` container.

### Call recordings (MVP sections 29, 32)

Recordings default to `local`: files in the `recordings` Docker volume, which
survives redeploys like the database does. They are never given a public URL —
the audio leaves the system only through `GET /quality/calls/{id}/recording`,
which is limited to Super Admin and Sales Manager and writes an audit entry
naming who listened.

| Name | Kind | Purpose |
|------|------|---------|
| `FETCH_PROVIDER_RECORDINGS` | variable | `true` to download the audio the telephony provider reports on its status webhook. Off by default — it is a network call to a URL a webhook supplied. |
| `RECORDING_RETENTION_DAYS` | variable | Days to keep audio; `0` (the default) keeps it indefinitely. The nightly worker task deletes expired audio and leaves the call, transcript and disposition intact. |
| `STORAGE_PROVIDER` | variable | `s3` to store in a bucket instead of on disk |
| `S3_BUCKET` / `S3_REGION` / `S3_ENDPOINT_URL` | variables | Bucket, region, and endpoint for S3-compatible stores |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | secrets | Bucket credentials |

**Set a retention period before the pilot.** Keeping customer voice data
forever is a decision, and leaving the default in place makes it by accident.

Back up the `recordings` volume alongside the database if the audio matters —
`docker compose cp` or a volume snapshot; the database backup does not include
it.

### Knowledge base (MVP section 18)

The `db` container is now **`pgvector/pgvector:pg16`** rather than
`postgres:16-alpine` — the same PostgreSQL 16 with the `vector` extension the
knowledge base migration creates.

> ⚠️ **Upgrading a server that already has data.** This swaps the image's base
> OS (Alpine/musl → Debian/glibc). The data directory format is unchanged, so
> the database starts, but glibc and musl sort text differently and indexes on
> text columns were built under the old ordering. On a server with real data,
> `pg_dump` before the deploy and restore into the new container, or run
> `REINDEX DATABASE swaraj_solar` afterwards. A server that has only ever held
> test data can just take the new image.

Load the curated content in `knowledge/` and check what landed:

```bash
docker compose -f docker-compose.prod.yml exec app \
  python -m backend.cli load-knowledge
```

Everything loads **unapproved**, and unapproved documents are never retrieved
on a call. A Super Admin or Sales Manager approves each one
(`POST /knowledge/documents/{slug}/approve`) after reading it. Editing an
approved document withdraws that approval. The shipped content is drafted from
`docs/MVP.md`, **not** from Swaraj's own approved material, so it needs review
line by line before any of it is approved.

Embeddings default to a mock (a hashed bag-of-words: offline, free, and about
as good as keyword search). For real semantic retrieval:

| Name | Kind | Purpose |
|------|------|---------|
| `EMBEDDING_PROVIDER` | variable | `voyage` to use real embeddings |
| `VOYAGE_API_KEY` | secret | Voyage AI key |
| `EMBEDDING_MODEL` | variable | Defaults to `voyage-3` (multilingual, covers Telugu) |

After switching provider, run `POST /knowledge/reindex` — vectors from two
models are not comparable, so search ignores chunks embedded by any model but
the active one, and retrieval returns nothing until they are rebuilt.

### AI provider configuration (optional until accounts exist)

The deployed stack defaults to `mock` for all three AI providers: conversations run end to end, no network calls are made and nothing is billed. To switch on the real pipeline, add these and redeploy:

| Name | Kind | Purpose |
|------|------|---------|
| `LLM_PROVIDER` | variable | `claude` to use the Claude API |
| `ANTHROPIC_API_KEY` | secret | Claude API key |
| `LLM_MODEL` | variable | Defaults to `claude-opus-5` |
| `VOICE_PROVIDER` | variable | `elevenlabs` for text-to-speech |
| `SPEECH_PROVIDER` | variable | `elevenlabs` to also use Scribe for speech-to-text |
| `ELEVENLABS_API_KEY` | secret | ElevenLabs API key |
| `ELEVENLABS_VOICE_ID` | variable | The chosen voice |
| `ELEVENLABS_MODEL_ID` | variable | Defaults to `eleven_v3` (multilingual, covers Telugu) |
| `ELEVENLABS_OUTPUT_FORMAT` | variable | `mp3_22050_32` by default; use `ulaw_8000` for PSTN legs |

Before going live on real calls:

- **Validate Telugu voice quality with native Telangana and Andhra speakers** (MVP section 37). ElevenLabs covers Telugu through its multilingual models, but its Indic showcase leads with other languages — compare against an Indic specialist before committing, which is a config change, not a rewrite.
- **Check the concurrency limit on your ElevenLabs plan.** Limits are plan-gated and the MVP targets 5–10 concurrent calls.
- **Model the cost per qualified lead.** At 2,000 leads/day, text-to-speech characters are a first-order cost. Static lines (the disclosure greeting, standard closings) are cached in-process rather than re-synthesized, which helps materially.
