"""Playing the corpus through the real orchestrator (MVP section 37).

Each conversation scenario gets its own customer, lead and call attempt, and
runs through `backend.ai.conversation` exactly as a real call does. Nothing
here reimplements the conversation — if the harness and production disagree,
the harness is wrong and the numbers are worthless.

Scenarios are isolated from each other. That matters most for opt-out: a
suppressed number is suppressed for the whole database, so a shared one
would let scenario 1 change the result of scenario 40.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.ai import conversation as orchestrator
from backend.ai.models import ConversationState
from backend.ai.numbers import to_number
from backend.calls.models import CallAttempt, CallState
from backend.customers.models import Customer
from backend.evaluation.corpus import Kind, Scenario
from backend.leads.models import Lead, LeadStatus, ServiceType

#: Comparing floats from YAML against floats from the parser.
TOLERANCE = 1e-9


@dataclass
class Failure:
    """One expectation that did not hold."""

    where: str       # "turn 2" or "final"
    key: str         # which expectation
    expected: object
    actual: object

    def __str__(self) -> str:
        return f"{self.where}: {self.key} expected {self.expected!r}, got {self.actual!r}"


@dataclass
class Result:
    scenario: Scenario
    passed: bool
    failures: list[Failure] = field(default_factory=list)
    latencies_ms: list[int] = field(default_factory=list)
    error: str | None = None
    skipped: str | None = None

    @property
    def ran(self) -> bool:
        return self.skipped is None


def _equal(expected, actual) -> bool:
    """Compare an expectation against reality, forgivingly but not loosely."""
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected) is bool(actual)
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        # The model may answer "6000" where the corpus says 6000.
        number = to_number(actual)
        return number is not None and abs(number - float(expected)) < TOLERANCE
    return str(expected).strip().lower() == str(actual).strip().lower()


def _enum_value(value) -> object:
    return value.value if hasattr(value, "value") else value


def _seed_call(db: Session, scenario: Scenario, index: int) -> CallAttempt:
    """A customer, lead and answered call for one scenario."""
    details = scenario.lead or {}
    # Unique per scenario: the customer table is keyed on phone number, and
    # two scenarios sharing one would share an opt-out.
    phone = f"+9199{index:08d}"

    customer = Customer(phone=phone, name=details.get("name") or "Evaluation")
    db.add(customer)
    db.flush()

    service = None
    if scenario.service:
        try:
            service = ServiceType(scenario.service)
        except ValueError as exc:
            raise ValueError(f"{scenario.id}: unknown service {scenario.service!r}") from exc

    lead = Lead(
        customer_id=customer.id,
        source="EVALUATION",
        consent_status="YES",
        city=details.get("city"),
        # The lead carries one language; "mixed" scenarios start in Telugu
        # and the orchestrator follows the customer from there.
        language="te-IN" if scenario.language == "mixed" else scenario.language,
        interested_service=service,
        status=LeadStatus.READY,
    )
    db.add(lead)
    db.flush()

    attempt = CallAttempt(
        lead_id=lead.id,
        customer_id=customer.id,
        provider="mock",
        provider_call_id=f"eval-{scenario.id}",
        to_number=phone,
        attempt_number=1,
        state=CallState.IN_PROGRESS,
    )
    db.add(attempt)
    db.flush()
    return attempt


def _check(expect: dict, where: str, *, conversation, decision, greeting, db) -> list[Failure]:
    """Compare one `expect` block against the conversation's state."""
    failures: list[Failure] = []

    def fail(key, expected, actual):
        failures.append(Failure(where=where, key=key, expected=expected, actual=actual))

    if "intent" in expect:
        actual = _enum_value(decision.intent) if decision else None
        if not _equal(expect["intent"], actual):
            fail("intent", expect["intent"], actual)

    if "service" in expect:
        actual = _enum_value(conversation.service)
        if not _equal(expect["service"], actual):
            fail("service", expect["service"], actual)

    if "language" in expect and not _equal(expect["language"], conversation.language):
        fail("language", expect["language"], conversation.language)

    if "reply_contains" in expect:
        reply = decision.reply if decision else ""
        if str(expect["reply_contains"]).lower() not in reply.lower():
            fail("reply_contains", expect["reply_contains"], reply)

    if "opening_contains" in expect:
        if str(expect["opening_contains"]).lower() not in (greeting or "").lower():
            fail("opening_contains", expect["opening_contains"], greeting)

    if "ended" in expect:
        ended = conversation.state is ConversationState.ENDED
        if bool(expect["ended"]) is not ended:
            fail("ended", expect["ended"], ended)

    for field_name, wanted in (expect.get("extracted") or {}).items():
        actual = (conversation.collected or {}).get(field_name)
        if not _equal(wanted, actual):
            fail(f"extracted.{field_name}", wanted, actual)

    if "disposition" in expect:
        attempt = db.get(CallAttempt, conversation.call_attempt_id)
        actual = _enum_value(attempt.disposition) if attempt else None
        if not _equal(expect["disposition"], actual):
            fail("disposition", expect["disposition"], actual)

    if "suppressed" in expect:
        customer = db.get(Customer, conversation.customer_id)
        actual = bool(customer and customer.opted_out)
        if bool(expect["suppressed"]) is not actual:
            fail("suppressed", expect["suppressed"], actual)

    if "classification" in expect:
        payload = orchestrator.structured_output(conversation)
        actual = payload.get("classification")
        if not _equal(expect["classification"], actual):
            fail("classification", expect["classification"], actual)

    return failures


def _run_number(scenario: Scenario) -> Result:
    """Assert that a spoken quantity reads as the figure the corpus says."""
    actual = to_number(scenario.spoken)
    expected = scenario.expect_value

    if expected is None:
        # The corpus says this is not a quantity; reading one would be worse
        # than reading nothing, because a wrong figure is never asked again.
        passed = actual is None
    else:
        passed = actual is not None and abs(actual - float(expected)) < TOLERANCE

    return Result(
        scenario=scenario,
        passed=passed,
        failures=[] if passed else [Failure("number", "value", expected, actual)],
    )


def _run_conversation(db: Session, scenario: Scenario, index: int) -> Result:
    attempt = _seed_call(db, scenario, index)
    conversation, greeting_speech = orchestrator.start_conversation(db, attempt)
    greeting = conversation.turns[0].text if conversation.turns else ""

    result = Result(scenario=scenario, passed=True)
    decision = None

    # `repeat` is expanded here so a scenario about a long call does not
    # have to write the same line out twenty times.
    script = [turn for turn in scenario.turns for _ in range(turn.repeat)]

    for position, turn in enumerate(script, start=1):
        if conversation.state is ConversationState.ENDED:
            # The conversation closed before the script ran out. Often that
            # is the point — a turn-limit or opt-out scenario is supposed to
            # end early. It is only a problem when the turns left unspoken
            # carried expectations, because those assertions never ran and
            # would otherwise be counted as passing.
            unchecked = [t for t in script[position - 1:] if t.expect]
            if unchecked:
                result.failures.append(
                    Failure(
                        f"turn {position}",
                        "conversation",
                        f"still open ({len(unchecked)} expectations left to check)",
                        "already ended",
                    )
                )
            break

        started = time.monotonic()
        decision, _ = orchestrator.handle_turn(db, conversation, text=turn.customer)
        result.latencies_ms.append(int((time.monotonic() - started) * 1000))

        if turn.expect:
            result.failures += _check(
                turn.expect, f"turn {position}",
                conversation=conversation, decision=decision, greeting=greeting, db=db,
            )

    if scenario.expect:
        result.failures += _check(
            scenario.expect, "final",
            conversation=conversation, decision=decision, greeting=greeting, db=db,
        )

    result.passed = not result.failures
    return result


def run_scenario(session_factory, scenario: Scenario, index: int) -> Result:
    """Run one scenario in its own transaction, rolled back afterwards."""
    if scenario.kind is Kind.NUMBER:
        return _run_number(scenario)

    session: Session = session_factory()
    try:
        return _run_conversation(session, scenario, index)
    except Exception:  # a broken scenario must not stop the run
        return Result(
            scenario=scenario, passed=False, error=traceback.format_exc(limit=4).strip()
        )
    finally:
        # Nothing the harness does should outlive it.
        session.rollback()
        session.close()


def run(
    session_factory,
    scenarios: list[Scenario],
    *,
    deterministic_only: bool = False,
) -> list[Result]:
    """Run the corpus, skipping model-graded scenarios when asked to."""
    results: list[Result] = []
    for index, scenario in enumerate(scenarios, start=1):
        if deterministic_only and not scenario.deterministic:
            results.append(
                Result(
                    scenario=scenario,
                    passed=True,
                    skipped="needs a real LLM provider",
                )
            )
            continue
        results.append(run_scenario(session_factory, scenario, index))
    return results
