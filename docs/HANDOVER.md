# Handover — state of the platform

Last updated: 2026-09-20 · 11 commits · 113 tests passing on SQLite and PostgreSQL
· 47 API endpoints · 4 migrations

---

## 1. What is built and working

Everything below is implemented, tested and deployed.

### Sprint 1 — leads and access
- JWT auth with the five MVP roles and RBAC; account lockout after 5 failed logins.
- Daily CSV/XLSX upload running the full MVP validation pipeline: required fields →
  Indian mobile validation and `+91` normalisation → in-file duplicates →
  previous-lead check → consent → DNC suppression.
- Per-upload summary counts; rejected rows downloadable with reasons.
- One customer record per phone number, so repeat uploads attach to history rather
  than creating new identities.
- Suppression list and audit log.

### Sprint 2 — campaigns and calling
- Campaigns with service, language, calling window (IST), concurrency, max attempts,
  priority and per-disposition retry rules; guarded state machine
  (start/pause/resume/stop/complete).
- Dispatcher: calls only inside the window, never exceeds free concurrency,
  re-checks opt-out before every dial, applies retry delays, exhausts after max
  attempts, and lets a customer's requested callback time override retry rules.
- Celery worker runs the dispatcher every 30s and closes finished campaigns.
- `TelephonyProvider` interface with a **mock** (default, places no real calls) and
  an **Exotel** implementation.
- Fixed MVP disposition codes, token-authenticated status webhook, inbound call
  handling that retrieves the caller's previous context.

### Sprint 3 — the AI voice agent
- `SpeechProvider` / `LLMProvider` / `VoiceProvider` interfaces. Mocks by default;
  **Claude** (structured outputs) and **ElevenLabs** (TTS + Scribe STT) implemented.
- Conversation orchestrator: audio → STT → LLM with lead context, qualification
  state and business rules → structured decision → TTS.
- Scripted Telugu AI disclosure opens every call. Qualification fields tracked per
  service across all ten service types. Structured payload + readable summary.
- Safety rails: deterministic opt-out and human-transfer detection, turn limit,
  provider failure hands to a human instead of leaving dead air.

### Sprints 4–8 (the parts not needing external accounts)
- **Lead scoring** with admin-configurable weights per service and HOT/WARM/COLD
  bands; the score picks the call disposition.
- **Solar/ROI engine** — the single approved calculation shared by website, AI and
  sales. Returns its assumptions with every answer.
- **Site surveys** booked automatically when a customer asks, with the MVP workflow.
- **Sales portal** — opportunities auto-assigned to the least-loaded executive,
  priority queue ordered by tier then score, notes and stage tracking.
- **Manager dashboard and funnel** with conversion rates and call metrics.
- **Website lead API** and public ROI calculator, running the same suppression checks.
- **Operations console** at `/` — sign-in, upload, campaigns, executive queue,
  surveys, calculator. Light and dark.

---

## 2. What is NOT built

| Gap | Why it matters |
|---|---|
| **TLS/HTTPS** | The API is plain HTTP. Passwords cross the network in the clear. **Do this before real customer data.** ~15 min with Caddy; needs a domain or a self-signed cert. |
| **RAG knowledge base** (MVP §18) | The AI answers from its prompt, not curated Swaraj content. Plan: pgvector in the existing database, not a second datastore. |
| **Recording storage + quality review UI** (MVP §29) | Transcripts are stored; recordings need object storage. |
| **Real-time SIP media streaming** | The conversation API is turn-based. Wire it when the telephony account exists. |
| **Mobile verification** | Responsive layout is implemented but only verified at desktop width. |

---

## 3. Deployment

GitHub Actions on push: tests → **migrations verified against real PostgreSQL** →
image built and pushed to GHCR → SSH deploy → health check.

Server runs four containers via `deploy/docker-compose.prod.yml`: `app`, `worker`,
`db` (Postgres 16), `redis`. Migrations run automatically on container start.
Deployment details and the required secrets: **`docs/DEPLOYMENT.md`**.

The deploy job is pinned to a GitHub environment named **`Test`**. Actions secrets
do not move between repos — recreate that environment and its values first, or the
deploy fails at the SSH step.

Console assets are content-fingerprinted and the page is served `no-store`, so a
deploy is picked up without a hard refresh.

---

## 4. Switching on the real providers

All three default to mocks that make no network calls and cost nothing. No code
change is needed — set environment variables and redeploy.

| To enable | Set |
|---|---|
| Real calls | `TELEPHONY_PROVIDER=exotel` + `EXOTEL_SID`, `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_CALLER_ID`, `PUBLIC_BASE_URL`, `TELEPHONY_WEBHOOK_TOKEN` |
| Claude | `LLM_PROVIDER=claude` + `ANTHROPIC_API_KEY` |
| ElevenLabs | `VOICE_PROVIDER=elevenlabs` (and optionally `SPEECH_PROVIDER=elevenlabs`) + `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` |

Before going live:

- **Confirm Exotel's exact API contract.** `backend/telephony/exotel.py` follows the
  published shape, but endpoints and callback field names vary by provisioned
  product. Also confirm the caller-ID/number series against current TRAI rules.
- **Validate Telugu voice quality** with native Telangana *and* Andhra speakers.
  ElevenLabs covers Telugu via its multilingual models but leads its Indic
  marketing with other languages. Sarvam AI is the Indic specialist worth
  A/B-testing — it is a config change, not a rewrite.
- **Model the cost per qualified lead.** At 2,000 leads/day, TTS characters are a
  first-order cost, likely beyond published ElevenLabs tiers. Static lines are
  cached in-process, which helps.

---

## 5. Decisions worth knowing before changing things

- **HOT/WARM/COLD are blue, not red/orange/blue.** The heat metaphor was tested and
  rejected: red against orange measures ΔE 5.6 under deuteranopia and 7.1 with
  normal vision, below the readable floor. The ordinal blue ramp passes in both
  light and dark, and the tier word always accompanies the dot.
- **Scoring replaced a Sprint 3 placeholder.** Qualified calls once returned a fixed
  `QUALIFIED_WARM`; the score now chooses the disposition.
- **Postgres, not a vector database, is the primary store.** When RAG arrives, use
  pgvector in the same database rather than adding LanceDB — one system to run,
  back up and secure, and embeddings can be joined to lead data.

### Two production incidents, and the guardrails added

1. **A migration re-created the shared `service_type` enum** and took the API down.
   PostgreSQL enums are database-wide; the second `CREATE TYPE` failed, the
   container exited mid-startup. Alembic autogenerate reproduces this every time.
   *Guardrail:* CI applies each migration in its own process against real
   PostgreSQL and exercises downgrade/re-apply.
2. **A shipped CSS fix appeared not to work** because browsers served the cached
   stylesheet. *Guardrail:* asset fingerprinting plus `no-store` on the page.

Both had the same root cause in my process — testing something in an environment
that differed from production in exactly the way that mattered.

---

## 6. Suggested next steps, in order

1. **TLS in front of the API** — the only item that blocks handling real customer data.
2. **Verify the mobile layout** if executives will use this on phones.
3. **Telugu voice evaluation** as soon as an ElevenLabs key exists; it may change
   the provider choice, so do it before tuning conversations around it.
4. **RAG knowledge base** with pgvector.
5. **Recording storage** and the quality-review UI.
