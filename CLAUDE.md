# Swaraj Solar — AI Sales Automation Platform

AI-powered outbound calling that qualifies solar leads in **Telugu and English**,
scores them, books site surveys and hands them to sales executives.

Full product scope: **`docs/MVP.md`**. Current state and next steps:
**`docs/HANDOVER.md`** — read that first.

## Architecture

Modular monolith + Celery workers, deliberately not microservices.

```
Daily CSV/XLSX + website API
        ↓  validation, dedupe, consent, DNC
   Lead management
        ↓
   Campaign engine ──→ dispatcher (calling window, concurrency, retries)
        ↓
   Telephony provider (mock | Exotel)
        ↓
   AI voice agent:  STT → LLM (+ lead context, qualification state) → TTS
        ↓
   Disposition → scoring → opportunity + site survey → sales queue
```

| Layer | Choice |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL (SQLite only for tests) |
| Worker | Celery + Redis, 30s dispatch tick |
| Console | Vanilla JS/CSS served by FastAPI at `/` — no build step |
| Deploy | Docker + GitHub Actions → SSH → docker compose |

## Module map (`backend/`)

`auth` roles & JWT · `leads` upload/validation · `customers` identity & history ·
`campaigns` engine + dispatcher · `calls` attempts, dispositions, webhooks, inbound ·
`ai` conversation orchestrator & providers · `scoring` configurable lead scoring ·
`solar_engine` approved sizing/ROI maths · `surveys` site visits ·
`sales` opportunities & assignment · `reports` dashboard & funnel ·
`compliance` suppression list & audit log · `telephony` provider abstraction ·
`knowledge` curated content, embeddings & retrieval ·
`quality` recordings, review & AI-quality metrics · `storage` object storage

## Commands

```bash
.venv/bin/python -m pytest -q                              # 400 tests
.venv/bin/uvicorn backend.main:app --reload                # API + console at /
.venv/bin/alembic -c database/alembic.ini upgrade head     # migrations
.venv/bin/celery -A workers.celery_app worker --beat       # dispatcher (needs Redis)
.venv/bin/python -m backend.cli create-admin --email x@y.com --password ... --name "..."
.venv/bin/python -m backend.cli load-knowledge             # knowledge/ -> database
.venv/bin/python -m backend.cli evaluate                   # MVP §37 corpus, §38 measures
.venv/bin/python -m backend.cli evaluate --all             # + model-graded (costs money)
```

## Rules this codebase holds to

These are product requirements, not style preferences. Breaking them is a bug.

1. **The LLM never does solar arithmetic.** System size, savings, subsidy, cost and
   payback come from `backend/solar_engine/` only. The system prompt forbids it and
   a test asserts the prohibition is present.
2. **The AI disclosure is scripted, not model-generated.** Every call opens by
   stating it is an AI assistant. A test asserts the LLM is not called first.
3. **Opt-out never depends on model judgement.** Telugu and English stop-calling
   phrases are matched deterministically *as well as* being asked of the LLM;
   either triggers suppression. The dispatcher re-checks suppression before every dial.
4. **No path bypasses the do-not-call list** — not the website API, not a re-upload.
5. **Providers are swappable.** Telephony, STT, LLM and TTS all sit behind
   interfaces with mock implementations as the default. Mocks make no network
   calls and cost nothing; real providers are switched on by environment variable.
6. **Migrations: never re-create a shared enum.** PostgreSQL enums are
   database-wide. `service_type` is shared across tables — reference it with
   `postgresql.ENUM(..., create_type=False)`. Autogenerate gets this wrong every
   time. See the note in `database/migrations/env.py`; CI catches it.
7. **Engineering constants need sign-off.** `backend/solar_engine/constants.py`
   values, especially subsidy slabs, are documented placeholders until Swaraj
   engineering approves them.
8. **Spoken quantities are read before they are stored.** MVP §37 requires
   "ఆరు వేలు", "6000" and "six thousand" to mean the same thing.
   `backend/ai/numbers.py` does that reading and the orchestrator applies it
   to every numeric field. This is not politeness: scoring compares
   `monthly_bill` with `float()` and treats a failure as "no", so an unread
   figure costs the customer their points with no error anywhere.
9. **The evaluation corpus is the gate before real calls.** MVP §37 requires
   100+ scripted tests and 50+ Telugu conversations to pass before a pilot.
   They live in `evaluation/scenarios/` as reviewable YAML. The deterministic
   half runs in CI for free; the model-graded half needs a real provider.
   Compliance scenarios must be deterministic — a test asserts it, because
   rule 3 means opt-out cannot be left to the model.
10. **Only approved knowledge reaches a customer.** Documents in
   `backend/knowledge/` are retrieved on a call only once a person has approved
   them, and editing one withdraws that approval. The content in `knowledge/`
   is drafted from the MVP document, not Swaraj's own material, so it ships
   unapproved. It also contains no figures, deliberately: rule 1 puts every
   number in the solar engine, and content the model reads aloud would
   otherwise be a way around that. A test enforces both.

## Testing

Tests run against SQLite for speed; **CI also runs the whole suite against real
PostgreSQL** and applies each migration in a separate process, because a
SQLite-only run hid a migration bug that took production down once.

Two code paths differ by database and are only both covered because of that:
the knowledge search ranks with pgvector's `<=>` on PostgreSQL and in Python on
SQLite. The same assertions run against each, so a difference between them
fails CI rather than production.
