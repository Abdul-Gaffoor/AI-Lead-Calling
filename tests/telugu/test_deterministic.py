"""Run the deterministic half of the corpus on every push (MVP §37).

These scenarios assert guarantees the orchestrator owns rather than things
the model decides, so they need no AI provider, cost nothing and cannot
flake. The model-graded half runs from `python -m backend.cli evaluate
--all` against a real provider.

Each scenario is its own test case, so a failure names the scenario rather
than reporting "the corpus is broken".
"""

import pytest

from backend.evaluation.corpus import load
from backend.evaluation.report import build
from backend.evaluation.runner import run_scenario
from backend.evaluation.sandbox import session_factory

DETERMINISTIC = [s for s in load() if s.deterministic]


@pytest.fixture(scope="module")
def sandbox():
    """One throwaway database for the module; each run rolls itself back."""
    return session_factory()


@pytest.mark.parametrize(
    "scenario", DETERMINISTIC, ids=[s.id for s in DETERMINISTIC]
)
def test_scenario(scenario, sandbox):
    result = run_scenario(sandbox, scenario, index=abs(hash(scenario.id)) % 10**7)

    if result.error:
        pytest.fail(f"{scenario.id} raised:\n{result.error}")
    assert result.passed, f"{scenario.description or scenario.id}\n" + "\n".join(
        f"  {failure}" for failure in result.failures
    )


def test_opt_out_is_never_missed():
    """
    MVP §30 and CLAUDE.md rule 3. Reported as one test as well as per
    scenario, because this is the line that decides whether a pilot can
    legally go ahead — a reviewer should not have to read 15 results.
    """
    factory = session_factory()
    compliance = [s for s in DETERMINISTIC if "compliance" in s.tags]
    assert compliance, "no compliance scenarios — the corpus lost them"

    results = [
        run_scenario(factory, scenario, index=index)
        for index, scenario in enumerate(compliance, start=1)
    ]
    report = build(results)

    missed = [r.scenario.id for r in results if not r.passed]
    assert report.compliance_clean, f"opt-out not honoured in: {missed}"
