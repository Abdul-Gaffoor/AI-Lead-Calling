# Swaraj Solar AI Sales Automation Platform

AI-powered solar lead calling, qualification, follow-up and sales handoff in **Telugu + English** for Swaraj Solar — automating daily outbound lead calling so sales executives spend their time on qualified customers, not dialing.

## What it does

- Ingests daily Excel/CSV lead uploads and website leads, with validation, deduplication, consent and DNC/opt-out checks
- Runs outbound calling campaigns over a compliant Indian cloud-telephony virtual business number
- An AI voice agent (STT → LLM → TTS) converses naturally in Telugu, English or a mix, identifies the required service, and qualifies the lead
- A central engineering-approved Solar/ROI engine produces indicative sizing and savings estimates (never the LLM)
- Scores and classifies leads (HOT/WARM/COLD), transfers live to sales executives when needed, books site surveys, and handles inbound callbacks with full context
- Gives managers a full funnel dashboard, recordings/transcripts with quality review, and compliance/audit trails

## Documentation

- **[docs/HANDOVER.md](docs/HANDOVER.md)** — current state, what is not built, deployment, provider switch-on, and suggested next steps. **Start here.**
- **[CLAUDE.md](CLAUDE.md)** — architecture, module map, commands and the invariants this codebase holds to.
- **[docs/MVP.md](docs/MVP.md)** — the complete MVP scope: users and roles, lead pipeline, telephony setup, AI voice architecture, per-service qualification workflows, scoring, dashboards, compliance, technical stack, repository structure, deployment architecture, sprint plan, test strategy and success criteria.

## Getting started (backend)

Requires Python 3.11+ and Docker (for local Postgres/Redis).

```bash
# 1. Infrastructure
docker compose up -d

# 2. Python environment
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 3. Configuration
cp .env.example .env   # then set a real JWT_SECRET

# 4. Database schema
.venv/bin/alembic -c database/alembic.ini upgrade head

# 5. First super-admin user
.venv/bin/python -m backend.cli create-admin \
  --email admin@swarajsolar.com --password <password> --name "Platform Admin"

# 6. Run the API
.venv/bin/uvicorn backend.main:app --reload
```

The interactive API docs are at http://localhost:8000/docs. Run the test suite with `.venv/bin/python -m pytest`.

## Deployment

Pushes to the deployment branches run the [CI & Deploy workflow](.github/workflows/deploy.yml): tests → Docker image build pushed to GHCR → SSH deploy to the server (compose stack with the API, Postgres and Redis; migrations run automatically on container start). Server credentials and app secrets are read from GitHub Actions secrets — see **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for the required secrets and one-time server setup.

### What works today

- **Auth & RBAC** — JWT login, five roles (Super Admin, Sales Manager, Lead Operator, Sales Executive, Service/Technical Executive), account lockout after repeated failed logins, user management endpoints.
- **Lead upload** — `POST /leads/uploads` accepts daily CSV/XLSX files (`GET /leads/template` provides the template) and runs the full validation pipeline: required-field checks, Indian mobile validation and `+91` normalization, in-file and previous-lead duplicate detection, consent checks, and DNC/opt-out suppression. Each upload returns the summary counts and rejected rows are downloadable with reasons (`/leads/uploads/{id}/rejections.csv`).
- **Customer history** — one customer record per phone number; repeated daily uploads attach to the same customer rather than creating new identities.
- **Opt-out/DNC** — `POST /compliance/opt-outs` adds a number to the suppression list; future uploads of that number are blocked automatically.
- **Audit log** — uploads, user creation and opt-outs are recorded for compliance.
- **Campaign engine** — create campaigns with a service, language, calling window (IST), concurrency, max attempts and per-disposition retry rules; queue leads from an upload or by id; drive them through `start` / `pause` / `resume` / `stop` / `complete`.
- **Call queue & retry engine** — a dispatcher places calls only inside the calling window, never exceeds the campaign's concurrency, retries `NO_ANSWER` / `BUSY` / `SWITCHED_OFF` on their own schedules, exhausts leads after max attempts, and honours a customer's requested callback time ahead of any retry rule. A Celery worker runs it every 30 seconds.
- **Telephony abstraction** — `TelephonyProvider` with `place_call` / `transfer` / `hangup` / `parse_webhook`. Ships with a **mock provider (default — places no real calls)** and an Exotel implementation to enable once the business number and KYC are in place.
- **Call dispositions & webhooks** — the fixed MVP disposition codes, a public `/calls/webhooks/{provider}` status callback (shared-token authenticated), and `POST /calls/{id}/disposition` for the AI agent or an executive to record the outcome. `DO_NOT_CALL` suppresses the number immediately, and the dispatcher re-checks suppression before every dial.

- **AI voice pipeline** — `SpeechProvider` / `LLMProvider` / `VoiceProvider` interfaces with mocks (default, no network calls or spend), a Claude implementation using structured outputs, and ElevenLabs for Telugu speech. The orchestrator runs the MVP's loop: customer audio → STT → LLM with qualification state and business rules → structured decision → TTS.
- **Conversation handling** — every call opens with the scripted Telugu AI disclosure (never model-generated, so it cannot be skipped), gathers per-service qualification fields without re-asking anything the lead record already holds, classifies the service, and produces a structured payload plus a human-readable summary. Outcomes map straight onto the Sprint 2 dispositions.
- **Safety rails** — opt-out and human-transfer requests are detected deterministically in Telugu and English rather than relying on model judgement; a provider failure hands off to a human instead of leaving dead air; conversations have a turn limit; and the system prompt forbids the model from ever calculating system sizes, savings, subsidies or payback (the approved solar engine does that in Sprint 8).

- **Lead scoring** — per-service rules with admin-configurable weights and HOT/WARM/COLD/UNQUALIFIED bands. A qualified conversation is scored automatically and its score decides the call disposition.
- **Solar/ROI engine** — one approved calculation shared by the website, the AI agent and sales: system size, roof area, generation, savings, cost range, subsidy, net investment and payback. Every assumption is returned with the answer, and the constants are marked for Swaraj engineering sign-off.
- **Site surveys** — booked automatically when a customer asks for one, then worked through REQUESTED → SCHEDULED → ASSIGNED → VISITED → COMPLETED → QUOTATION REQUIRED.
- **Sales portal** — qualified leads become opportunities, auto-assigned to the least-loaded executive, with a priority queue (hottest first), notes, follow-ups and stage tracking.
- **Manager dashboard & funnel** — today's numbers (uploaded, attempted, connected, qualified, HOT/WARM/COLD, surveys, transfers, callbacks, opt-outs), the lead-to-order funnel with conversion rates, and call metrics.
- **Inbound callbacks** — a returning caller is identified by number and their previous lead, last call and open opportunity are retrieved so the conversation continues instead of starting over.
- **Website intake** — an API-key-protected endpoint for swarajsolar.com forms plus a public ROI calculator, running the same validation and suppression checks as an Excel upload.
- **Operations console** — a web UI at `/` covering sign-in, lead upload with the validation summary, campaign creation and control, the executive queue, site surveys and the solar calculator.

Run the dispatcher locally with `.venv/bin/celery -A workers.celery_app worker --beat` (needs Redis), or trigger a single tick with `POST /campaigns/{id}/dispatch`.

## Technical stack (planned)

Next.js frontend · Python FastAPI backend · PostgreSQL (+ pgvector for RAG) · Redis + Celery workers · Indian cloud telephony/SIP · pluggable STT/LLM/TTS providers · Docker + Terraform/OpenTofu.

Architecture: **modular monolith + workers** for the MVP.

## Status

🚧 In development — see the sprint-by-sprint build sequence in [docs/MVP.md](docs/MVP.md#36-build-sequence).

- ✅ **Sprint 1** — authentication, DB, customer/lead model, Excel/CSV upload, validation, duplicate and opt-out handling
- ✅ **Sprint 2** — campaign engine, call queue and retry scheduler, telephony provider abstraction (mock + Exotel), call webhooks and dispositions
- ✅ **Sprint 3** — AI voice pipeline: pluggable STT/LLM/TTS (mock, Claude, ElevenLabs), conversation orchestrator, AI disclosure, service classification, structured extraction
- ✅ **Sprints 4–5** — qualification field sets for all ten services, driving the AI's questions and the scoring rules
- ✅ **Sprint 6** — lead scoring with configurable weights, AI summaries, executive assignment, callbacks, site-survey module
- ✅ **Sprint 7** — manager dashboard, funnel reporting, call metrics, transcripts
- 🟡 **Sprint 8** — website lead API, central ROI engine, TLS/HTTPS and the RAG knowledge base done; **remaining: recording storage, production pilot**

### Not yet built

- **Recording storage and quality review UI** (MVP section 29) — transcripts are stored; recordings need object storage.
- **Live media streaming** — the conversation API is turn-based; real-time SIP media streaming is wired when the telephony account exists.
