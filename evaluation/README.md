# Evaluation corpus — MVP section 37

> **This corpus has not been reviewed by a native Telugu speaker.**
> It was drafted from the MVP document to give the harness something real to
> run against. Before the pilot, somebody who speaks Telangana and Andhra
> Telugu must read every `customer:` line and correct it. Treat the scores
> until then as a measure of the plumbing, not of the Telugu.

MVP section 37 puts a gate in front of calling real customers:

> 100+ scripted AI tests · 50+ Telugu conversations · Telangana Telugu ·
> Andhra Telugu · Telugu + English · English · numbers · units · bills ·
> locations · names · PIN codes · interruptions · background noise ·
> customer changing topic · customer asking human · customer saying don't call

This directory is that gate. Each file is a set of scenarios; each scenario
is a conversation played through the real orchestrator in
`backend/ai/conversation.py`, with the expected outcome written down beside
it.

## Running it

```bash
.venv/bin/python -m backend.cli evaluate                 # deterministic only, free
.venv/bin/python -m backend.cli evaluate --all           # includes model-graded
.venv/bin/python -m backend.cli evaluate --json out.json # machine-readable
```

The deterministic subset also runs in CI on every push, because it needs no
AI provider and costs nothing.

## The two kinds of scenario

**Deterministic** (`deterministic: true`) — the outcome is guaranteed by the
orchestrator, not by the model: opt-out matching, human-transfer detection,
the scripted AI disclosure, number reading, the turn limit. These are the
rules `CLAUDE.md` calls product requirements. They must pass at **100%**, and
they run against the mock providers so they cost nothing and cannot flake.

**Model-graded** (the default) — the outcome depends on the LLM reading the
customer correctly: which service they want, what they said their bill was,
whether they are a HOT lead. These only run when a real provider is
configured, and they produce the MVP section 38 accuracy numbers. They cost
money per run.

A deterministic scenario that happens to need the model will fail loudly
rather than pass by accident.

## Writing a scenario

```yaml
- id: res-bill-telugu-words
  description: Bill given in Telugu words rather than digits
  language: te-IN
  dialect: telangana          # telangana | andhra | mixed | english
  service: RESIDENTIAL_SOLAR  # the service this conversation is about
  tags: [numbers, bill]
  lead:                       # optional; seeds the lead the AI is calling
    name: Ramesh
    city: Miyapur
  turns:
    - customer: "నాకు ఇంటికి సోలార్ కావాలి"
    - customer: "నెలకి ఆరు వేలు బిల్లు వస్తుంది"
      expect:
        extracted:
          monthly_bill: 6000
  expect:
    intent: QUALIFIED
    classification: HOT
```

Everything under `expect` is optional — assert only what the scenario is
about. A scenario testing number reading should not also pin the reply text.

| Key | Meaning |
|---|---|
| `expect.intent` | The intent the conversation ends on |
| `expect.service` | The service classified |
| `expect.classification` | HOT / WARM / COLD / UNQUALIFIED |
| `expect.disposition` | The call disposition recorded |
| `expect.extracted` | Fields and values that must be captured |
| `expect.reply_contains` | Substring the AI's reply must contain |
| `expect.suppressed` | `true` when the number must land on the DNC list |

Per-turn `expect` blocks assert the state *after that turn*; the
scenario-level one asserts the end state.

## Adding Telugu

Two things matter more than volume:

1. **Write what people actually say**, not textbook Telugu. "కరెంట్ బిల్"
   not "విద్యుత్ బిల్లు" if that is what the customer says. Code-mixing is
   normal and should be well represented — the corpus has a `mixed` dialect
   bucket for exactly that reason.
2. **Keep dialects separated** by the `dialect` field. The report breaks
   scores down by bucket, so a model that handles Andhra Telugu and fails on
   Telangana shows up as two numbers rather than one average that hides it.
