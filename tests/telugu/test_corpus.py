"""The corpus itself has to stay trustworthy (MVP §37).

A corpus that silently stops loading, loses its Telugu, or drifts below the
thresholds §37 sets is worse than no corpus: the harness still reports a
pass rate, and the pass rate still looks fine.
"""

import pytest

from backend.core.config import settings
from backend.evaluation.corpus import DIALECTS, Kind, load

SCENARIOS = load()


def test_the_corpus_loads():
    """Every file parses and every scenario validates."""
    assert SCENARIOS, "no scenarios loaded"


def test_there_are_a_hundred_scripted_tests():
    """MVP §37: "100+ scripted AI tests"."""
    assert len(SCENARIOS) >= 100, (
        f"§37 asks for 100+ scripted tests; the corpus has {len(SCENARIOS)}"
    )


def test_there_are_fifty_telugu_conversations():
    """MVP §37: "50+ Telugu conversations"."""
    telugu = [s for s in SCENARIOS if s.is_telugu]
    assert len(telugu) >= 50, (
        f"§37 asks for 50+ Telugu conversations; the corpus has {len(telugu)}"
    )


@pytest.mark.parametrize("dialect", DIALECTS)
def test_every_dialect_bucket_is_populated(dialect):
    """§37 names four buckets. A bucket with nothing in it scores 100%."""
    covered = [s for s in SCENARIOS if s.dialect == dialect]
    assert covered, f"no scenarios for the {dialect} bucket"


@pytest.mark.parametrize(
    "requirement",
    [
        "numbers",      # "ఆరు వేలు" / "6000" / "six thousand"
        "units",
        "pincode",
        "noise",
        "interruption",
        "topic-change",
        "human-transfer",
        "opt-out",
    ],
)
def test_section_37_requirements_are_covered(requirement):
    """Each thing §37 lists by name has at least one scenario."""
    covered = [s for s in SCENARIOS if requirement in s.tags]
    assert covered, f"§37 names {requirement!r} but no scenario is tagged with it"


def test_the_three_number_forms_from_section_37_are_present():
    """§37 spells out "ఆరు వేలు", "6000" and "six thousand" explicitly."""
    spoken = {s.spoken for s in SCENARIOS if s.kind is Kind.NUMBER}
    for form in ("ఆరు వేలు", "6000", "six thousand"):
        assert form in spoken, f"§37 names {form!r}; no number case covers it"


def test_scenario_ids_are_unique():
    """`load` enforces this, but a duplicate would silently drop a case."""
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_the_turn_limit_scenario_actually_exceeds_the_limit():
    """
    `robust-turn-limit` only tests anything while its turn count is above
    MAX_CONVERSATION_TURNS. Raising that setting without touching the
    scenario would turn it into a test that asserts nothing.
    """
    scenario = next(s for s in SCENARIOS if s.id == "robust-turn-limit")
    turns = sum(turn.repeat for turn in scenario.turns)
    assert turns > settings.max_conversation_turns, (
        f"the scenario runs {turns} turns but the limit is now "
        f"{settings.max_conversation_turns} — raise the scenario to match"
    )


def test_compliance_scenarios_are_deterministic():
    """
    CLAUDE.md rule 3: opt-out must not depend on model judgement. A
    compliance scenario that only passes with a real LLM is not testing the
    guarantee — except the ones deliberately tagged as coverage gaps.
    """
    soft = [
        s
        for s in SCENARIOS
        if "compliance" in s.tags
        and not s.deterministic
        and "coverage-gap" not in s.tags
    ]
    assert not soft, (
        "these compliance scenarios need the model, which rule 3 forbids: "
        f"{[s.id for s in soft]}"
    )
