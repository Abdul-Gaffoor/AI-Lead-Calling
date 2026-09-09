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

- **[docs/MVP.md](docs/MVP.md)** — the complete MVP scope: users and roles, lead pipeline, telephony setup, AI voice architecture, per-service qualification workflows, scoring, dashboards, compliance, technical stack, repository structure, deployment architecture, sprint plan, test strategy and success criteria.

## Technical stack (planned)

Next.js frontend · Python FastAPI backend · PostgreSQL (+ pgvector for RAG) · Redis + Celery workers · Indian cloud telephony/SIP · pluggable STT/LLM/TTS providers · Docker + Terraform/OpenTofu.

Architecture: **modular monolith + workers** for the MVP.

## Status

📋 Planning complete — see the sprint-by-sprint build sequence in [docs/MVP.md](docs/MVP.md#36-build-sequence). Implementation starts with Sprint 1 (auth, data model, lead upload and validation).
