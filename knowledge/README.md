# Swaraj knowledge base

Markdown source for what the AI voice agent is allowed to say (MVP section 18).
Loaded into the database with:

```bash
.venv/bin/python -m backend.cli load-knowledge
```

Two rules this directory holds to:

1. **Nothing here is approved.** Loading a file creates an *unapproved*
   document, and unapproved documents are never retrieved. A curator approves
   each one in the console after reading it. Editing an approved document
   withdraws its approval, because approval is a person vouching for specific
   words.
2. **No figures.** No prices, subsidy amounts, system sizes, savings, payback
   periods or capacities appear in this content, and none should be added. The
   approved solar engine produces every number this business quotes; the model
   is forbidden from doing that arithmetic, and content it reads back to the
   customer would be a way around that rule. Describe *how* something works and
   say the team confirms the figures.

The content below was drafted from `docs/MVP.md` and general industry practice.
**It is not Swaraj's own approved material** and must be reviewed line by line
against what the company actually offers before any of it is approved.
