# Handover — state of the platform

Last updated: 2026-09-20 · 170 tests passing on SQLite and PostgreSQL
· 6 migrations

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
- **Knowledge base / RAG** (MVP §18) — curated Swaraj content in the same
  PostgreSQL via pgvector, retrieved per question and put in front of the LLM
  with an instruction to answer from it and nothing else. `EmbeddingProvider`
  follows the other provider interfaces: the default mock is a hashed
  bag-of-words, so retrieval works offline and costs nothing, at roughly
  keyword quality. **Nothing is retrieved until a person approves it**, and
  editing an approved document withdraws that approval.
- **Recordings and quality review** (MVP §29) — recordings in object storage
  (`local` on a volume by default, `s3` when they outgrow a disk), and a review
  screen putting the recording, transcript, extracted fields, summary, score and
  disposition in one place with the five MVP fault codes. Playback is limited to
  Super Admin and Sales Manager and is audited. A nightly task enforces
  `RECORDING_RETENTION_DAYS`, which starts at "keep indefinitely".
- **Website lead API** and public ROI calculator, running the same suppression checks.
- **Operations console** at `/` — sign-in, upload, campaigns, executive queue,
  surveys, calculator. Light and dark, verified down to 360px in a real browser
  (`tools/responsive_audit.py`): no page scrolls sideways, the drawer dims and
  dismisses the console behind it, and controls meet tap-target sizes.

---

## 2. What is NOT built

| Gap | Why it matters |
|---|---|
| **Real-time SIP media streaming** | The conversation API is turn-based. Wire it when the telephony account exists. |

---

## 3. Deployment

GitHub Actions on push: tests → **migrations verified against real PostgreSQL** →
image built and pushed to GHCR → SSH deploy → health check.

Server runs five containers via `deploy/docker-compose.prod.yml`: `app`, `worker`,
`db` (PostgreSQL 16 with pgvector), `redis` and `caddy`. Migrations run automatically on container start.
Deployment details and the required secrets: **`docs/DEPLOYMENT.md`**.

The deploy job is pinned to a GitHub environment named **`Test`**. Actions secrets
do not move between repos — recreate that environment and its values first, or the
deploy fails at the SSH step.

`caddy` terminates TLS: it redirects port 80, renews its
certificate by itself, and is the only thing exposed to the internet — the API
publishes 8000 on loopback. With a `DOMAIN` set the certificate is a publicly
trusted Let's Encrypt one; with none it falls back to the server's own host and
Caddy's internal CA, which encrypts but makes browsers warn. **Set a domain
before the customer pilot.** The deploy fails if HTTPS does not answer.

Console assets are content-fingerprinted and the page is served `no-store`, so a
deploy is picked up without a hard refresh.

---

## 4. Switching on the real providers

Every provider — telephony, speech, LLM, voice, embeddings — defaults to a mock
that makes no network calls and costs nothing. No code change is needed; set
environment variables and redeploy. `docs/DEPLOYMENT.md` has the full list,
including the embedding and recording-storage settings.

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
- **Postgres, not a vector database, is the primary store.** RAG uses pgvector in
  the same database rather than a second datastore — one system to run, back up
  and secure, and embeddings can be joined to lead data. The `db` image is now
  `pgvector/pgvector:pg16`; see `docs/DEPLOYMENT.md` before taking that image
  onto a server that already holds data.
- **The knowledge base ships switched off, and holds no numbers.** Approval is a
  person vouching for specific words, so editing a document withdraws it. And
  no figure appears in the curated content: the solar engine owns every number,
  and content the model reads aloud would otherwise be a way around that rule.
- **Review flags are fixed codes, not free text.** MVP §38 measures the platform
  on Telugu understanding, field capture and classification accuracy; those
  numbers only exist if the faults are countable. An "incorrect" verdict must
  name at least one fault for the same reason.
- **Vectors are only compared within one embedding model.** Chunks record the
  model that produced them and search filters on the active one, so switching
  provider degrades to "no knowledge" — which the AI handles — rather than to
  confident nonsense from an incomparable vector space.

### Two production incidents, and the guardrails added

1. **A migration re-created the shared `service_type` enum** and took the API down.
   PostgreSQL enums are database-wide; the second `CREATE TYPE` failed, the
   container exited mid-startup. Alembic autogenerate reproduces this every time.
   *Guardrail:* CI applies each migration in its own process against real
   PostgreSQL and exercises downgrade/re-apply.
2. **A shipped CSS fix appeared not to work** because browsers served the cached
   stylesheet. *Guardrail:* asset fingerprinting plus `no-store` on the page.
3. **The guardrail from incident 1 was not running.** CI's "apply each migration
   in its own process" loop started from an empty string and compared it to
   `alembic current`, which prints nothing on a fresh database — so it matched
   on the first pass and exited before applying anything, leaving the
   all-at-once `upgrade head` as the only thing that ran. That is precisely the
   single-process case that hides a duplicate `CREATE TYPE`. Fixed by starting
   from a sentinel; verified against a real PostgreSQL, where all five
   revisions now apply one per process.

Both had the same root cause in my process — testing something in an environment
that differed from production in exactly the way that mattered.

---

## 6. Suggested next steps, in order

1. **Review and approve the knowledge base.** `knowledge/` is drafted from
   `docs/MVP.md`, not from Swaraj's own material, and until somebody reads each
   document and approves it the AI has no company content at all. This is the
   cheapest large improvement to answer quality available.
2. **Telugu voice evaluation** as soon as an ElevenLabs key exists; it may change
   the provider choice, so do it before tuning conversations around it.
3. **Point a domain at the server** and set `DOMAIN` / `ACME_EMAIL`, so TLS uses a
   publicly trusted certificate instead of the internal fallback.
4. **Decide a recording retention period** and set `RECORDING_RETENTION_DAYS`.
   The default keeps customer voice data forever, which is a decision nobody
   should make by leaving a default alone.
5. **Real embeddings** (`EMBEDDING_PROVIDER=voyage`) once there is a key. The
   mock cannot match meaning across different words, so "how much do I save"
   will not find a passage about payback. Run `POST /knowledge/reindex` after
   switching.
