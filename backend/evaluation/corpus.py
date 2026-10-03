"""Loading and validating the evaluation corpus (MVP section 37).

The corpus lives in `evaluation/scenarios/` as YAML so a Telugu speaker can
read and extend it without touching Python. This module turns those files
into checked objects and refuses malformed ones loudly — a scenario that
silently fails to load is a test that silently stops running.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CORPUS_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "scenarios"

#: MVP section 37's language buckets. Scores are reported per bucket, because
#: an average across all of them hides a dialect the model cannot handle.
DIALECTS = ("telangana", "andhra", "mixed", "english")
LANGUAGES = ("te-IN", "en-IN", "mixed")


class Kind(str, enum.Enum):
    CONVERSATION = "conversation"
    NUMBER = "number"


#: Keys allowed in an `expect` block. Anything else is a typo, and a typo in
#: an expectation is an assertion that never runs.
EXPECT_KEYS = frozenset(
    {
        "intent",
        "service",
        "classification",
        "disposition",
        "extracted",
        "reply_contains",
        "opening_contains",
        "suppressed",
        "language",
        "ended",
    }
)

SCENARIO_KEYS = frozenset(
    {
        "id",
        "kind",
        "description",
        "language",
        "dialect",
        "service",
        "tags",
        "lead",
        "turns",
        "expect",
        "deterministic",
        "spoken",
    }
)


class CorpusError(Exception):
    """Raised when a scenario file cannot be trusted."""


@dataclass(frozen=True)
class Turn:
    customer: str
    expect: dict = field(default_factory=dict)
    #: Say the same thing this many times. For scenarios about what happens
    #: over a long call, where writing the line out twenty times would only
    #: make the file harder to read.
    repeat: int = 1


@dataclass(frozen=True)
class Scenario:
    id: str
    kind: Kind
    source: str
    description: str = ""
    language: str = "te-IN"
    dialect: str = "telangana"
    service: str | None = None
    tags: tuple[str, ...] = ()
    lead: dict = field(default_factory=dict)
    turns: tuple[Turn, ...] = ()
    expect: dict = field(default_factory=dict)
    deterministic: bool = False
    spoken: str | None = None
    #: For `kind: number` only — the figure `spoken` must read as, or None
    #: when it must read as nothing. Kept apart from `expect` because that
    #: one is a mapping and this one is a scalar.
    expect_value: float | None = None

    @property
    def is_telugu(self) -> bool:
        return self.language in ("te-IN", "mixed")


def _validate(raw: dict, source: str) -> None:
    if unknown := set(raw) - SCENARIO_KEYS:
        raise CorpusError(f"{source}: {raw.get('id', '?')} has unknown keys {sorted(unknown)}")
    if not raw.get("id"):
        raise CorpusError(f"{source}: a scenario is missing its id")

    scenario_id = raw["id"]
    kind = raw.get("kind", Kind.CONVERSATION.value)
    if kind not in (k.value for k in Kind):
        raise CorpusError(f"{source}: {scenario_id} has unknown kind {kind!r}")

    if raw.get("dialect", "telangana") not in DIALECTS:
        raise CorpusError(
            f"{source}: {scenario_id} has dialect {raw.get('dialect')!r}, "
            f"expected one of {DIALECTS}"
        )
    if raw.get("language", "te-IN") not in LANGUAGES:
        raise CorpusError(
            f"{source}: {scenario_id} has language {raw.get('language')!r}, "
            f"expected one of {LANGUAGES}"
        )

    if kind == Kind.NUMBER.value:
        if "spoken" not in raw:
            raise CorpusError(f"{source}: {scenario_id} is a number case with no `spoken`")
        if "expect" not in raw:
            raise CorpusError(f"{source}: {scenario_id} is a number case with no `expect`")
        return

    for turn in raw.get("turns") or []:
        if "customer" not in turn:
            raise CorpusError(f"{source}: {scenario_id} has a turn with no `customer`")
        if unknown_turn := set(turn) - {"customer", "expect", "repeat"}:
            raise CorpusError(
                f"{source}: {scenario_id} turn has unknown keys {sorted(unknown_turn)}"
            )
        if int(turn.get("repeat", 1)) < 1:
            raise CorpusError(f"{source}: {scenario_id} has a turn repeated < 1 time")
        if unknown := set(turn.get("expect") or {}) - EXPECT_KEYS:
            raise CorpusError(
                f"{source}: {scenario_id} turn expects unknown {sorted(unknown)}"
            )
    if unknown := set(raw.get("expect") or {}) - EXPECT_KEYS:
        raise CorpusError(f"{source}: {scenario_id} expects unknown {sorted(unknown)}")


def _build(raw: dict, source: str) -> Scenario:
    kind = Kind(raw.get("kind", Kind.CONVERSATION.value))
    return Scenario(
        id=raw["id"],
        kind=kind,
        source=source,
        description=raw.get("description", ""),
        language=raw.get("language", "te-IN"),
        dialect=raw.get("dialect", "telangana"),
        service=raw.get("service"),
        tags=tuple(raw.get("tags") or ()),
        lead=raw.get("lead") or {},
        turns=tuple(
            Turn(
                customer=t["customer"],
                expect=t.get("expect") or {},
                repeat=int(t.get("repeat", 1)),
            )
            for t in (raw.get("turns") or [])
        ),
        expect=(raw.get("expect") or {}) if kind is Kind.CONVERSATION else {},
        deterministic=bool(raw.get("deterministic")) or kind is Kind.NUMBER,
        spoken=raw.get("spoken") if kind is Kind.NUMBER else None,
        expect_value=raw.get("expect") if kind is Kind.NUMBER else None,
    )


def load(directory: Path | None = None) -> list[Scenario]:
    """Every scenario in the corpus, in file then document order."""
    directory = directory or CORPUS_DIR
    if not directory.is_dir():
        raise CorpusError(f"No corpus directory at {directory}")

    scenarios: list[Scenario] = []
    seen: dict[str, str] = {}

    for path in sorted(directory.glob("*.yaml")):
        try:
            documents = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise CorpusError(f"{path.name} is not valid YAML: {exc}") from exc
        if documents is None:
            continue
        if not isinstance(documents, list):
            raise CorpusError(f"{path.name} must hold a list of scenarios")

        for raw in documents:
            if not isinstance(raw, dict):
                raise CorpusError(f"{path.name} has a scenario that is not a mapping")
            _validate(raw, path.name)
            if raw["id"] in seen:
                raise CorpusError(
                    f"{path.name}: id {raw['id']!r} already used in {seen[raw['id']]}"
                )
            seen[raw["id"]] = path.name
            scenarios.append(_build(raw, path.name))

    if not scenarios:
        raise CorpusError(f"No scenarios found in {directory}")
    return scenarios
