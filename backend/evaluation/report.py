"""Turning run results into the MVP section 38 measures.

Section 38 is explicit that "AI sounds impressive" is not a result. These
are the numbers that replace that judgement. Four of its eleven measures
need live calls and cannot come from a harness — connection rate, cost per
qualified lead, human calling hours saved, customer opt-out rate — so they
are named as missing rather than quietly dropped.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from backend.evaluation.corpus import DIALECTS
from backend.evaluation.runner import Result

#: Measures that only a pilot can produce. Listed so a reader of the report
#: can see what it does not cover.
NEEDS_LIVE_CALLS = (
    "Call connection rate",
    "Cost per qualified lead",
    "Human calling hours saved",
    "Customer opt-out rate",
)


@dataclass
class Tally:
    passed: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed

    @property
    def rate(self) -> float | None:
        return None if not self.total else self.passed / self.total


@dataclass
class Report:
    results: list[Result]
    overall: Tally = field(default_factory=Tally)
    by_dialect: dict[str, Tally] = field(default_factory=dict)
    by_tag: dict[str, Tally] = field(default_factory=dict)
    measures: dict[str, Tally] = field(default_factory=dict)
    latencies_ms: list[int] = field(default_factory=list)
    skipped: int = 0
    errors: int = 0

    @property
    def compliance_clean(self) -> bool:
        """Opt-out must be perfect. Nothing else in here is pass/fail."""
        tally = self.by_tag.get("compliance")
        return tally is None or tally.failed == 0


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


#: Which failing expectation counts against which section 38 measure. A
#: scenario can inform more than one.
_MEASURE_KEYS = {
    "Telugu understanding accuracy": ("intent", "service", "language"),
    "Required-field capture accuracy": ("extracted",),
    "Lead-classification accuracy": ("classification", "disposition"),
    "Human escalation accuracy": ("intent",),
}


def build(results: list[Result]) -> Report:
    report = Report(results=results)
    by_dialect: dict[str, Tally] = defaultdict(Tally)
    by_tag: dict[str, Tally] = defaultdict(Tally)
    measures: dict[str, Tally] = defaultdict(Tally)

    for result in results:
        if not result.ran:
            report.skipped += 1
            continue
        if result.error:
            report.errors += 1

        scenario = result.scenario
        passed = result.passed
        report.overall.passed += passed
        report.overall.failed += not passed
        report.latencies_ms += result.latencies_ms

        tally = by_dialect[scenario.dialect]
        tally.passed += passed
        tally.failed += not passed

        for tag in scenario.tags:
            tag_tally = by_tag[tag]
            tag_tally.passed += passed
            tag_tally.failed += not passed

        failed_keys = {f.key.split(".")[0] for f in result.failures}
        for measure, keys in _MEASURE_KEYS.items():
            # A scenario only informs a measure it actually exercises.
            if not _exercises(scenario, keys, measure):
                continue
            measure_tally = measures[measure]
            # A scenario that blew up measured nothing, so it cannot score
            # as a pass just because it recorded no specific failure.
            if result.error or (failed_keys & set(keys)):
                measure_tally.failed += 1
            else:
                measure_tally.passed += 1

    report.by_dialect = dict(by_dialect)
    report.by_tag = dict(by_tag)
    report.measures = dict(measures)
    return report


def _exercises(scenario, keys: tuple[str, ...], measure: str) -> bool:
    """Does this scenario assert anything the measure is about?"""
    if measure == "Human escalation accuracy":
        return "human-transfer" in scenario.tags or "escalation" in scenario.tags
    asserted = set(scenario.expect)
    for turn in scenario.turns:
        asserted |= set(turn.expect)
    return bool(asserted & set(keys))


def _pct(tally: Tally | None) -> str:
    if tally is None or tally.rate is None:
        return "     —"
    return f"{tally.rate * 100:5.1f}%"


def render(report: Report) -> str:
    """The report as a human reads it."""
    lines: list[str] = []
    add = lines.append

    total = report.overall.total
    add("=" * 66)
    add("  Telugu evaluation — MVP §37 corpus, §38 measures")
    add("=" * 66)
    add("")
    add(f"  Ran        {total}")
    add(f"  Passed     {report.overall.passed}   ({_pct(report.overall).strip()})")
    add(f"  Failed     {report.overall.failed}")
    if report.skipped:
        add(f"  Skipped    {report.skipped}   (model-graded; no real LLM configured)")
    if report.errors:
        add(f"  Errored    {report.errors}")
    add("")

    add("  MVP §38 measures")
    add("  " + "-" * 62)
    for measure in _MEASURE_KEYS:
        tally = report.measures.get(measure)
        count = f"{tally.passed}/{tally.total}" if tally else "0/0"
        add(f"    {measure:<36} {_pct(tally)}  ({count})")

    p50 = _percentile(report.latencies_ms, 0.50)
    p95 = _percentile(report.latencies_ms, 0.95)
    latency = "—" if p50 is None else f"p50 {p50} ms · p95 {p95} ms"
    add(f"    {'AI response latency':<36} {latency}")
    add("")
    add("    Not measurable without live calls:")
    for measure in NEEDS_LIVE_CALLS:
        add(f"      · {measure}")
    add("")

    add("  By dialect (§37 buckets)")
    add("  " + "-" * 62)
    for dialect in DIALECTS:
        tally = report.by_dialect.get(dialect)
        count = f"{tally.passed}/{tally.total}" if tally else "0/0"
        add(f"    {dialect:<36} {_pct(tally)}  ({count})")
    add("")

    compliance = report.by_tag.get("compliance")
    if compliance:
        state = "clean" if compliance.failed == 0 else f"{compliance.failed} FAILING"
        add(f"  Opt-out and compliance: {compliance.passed}/{compliance.total} — {state}")
        if compliance.failed:
            add("  A missed opt-out is a compliance breach. Fix before any pilot.")
        add("")

    failures = [r for r in report.results if r.ran and not r.passed]
    if failures:
        add("  Failures")
        add("  " + "-" * 62)
        for result in failures:
            add(f"    {result.scenario.id}  ({result.scenario.source})")
            if result.error:
                for line in result.error.splitlines()[-3:]:
                    add(f"        ! {line.strip()}")
            for failure in result.failures:
                add(f"        {failure}")
        add("")

    return "\n".join(lines)


def to_dict(report: Report) -> dict:
    """The same report, for a dashboard or a trend line across runs."""
    return {
        "total": report.overall.total,
        "passed": report.overall.passed,
        "failed": report.overall.failed,
        "skipped": report.skipped,
        "errors": report.errors,
        "pass_rate": report.overall.rate,
        "measures": {
            name: {"passed": t.passed, "total": t.total, "rate": t.rate}
            for name, t in report.measures.items()
        },
        "latency_ms": {
            "p50": _percentile(report.latencies_ms, 0.50),
            "p95": _percentile(report.latencies_ms, 0.95),
            "samples": len(report.latencies_ms),
        },
        "by_dialect": {
            name: {"passed": t.passed, "total": t.total, "rate": t.rate}
            for name, t in report.by_dialect.items()
        },
        "by_tag": {
            name: {"passed": t.passed, "total": t.total, "rate": t.rate}
            for name, t in report.by_tag.items()
        },
        "needs_live_calls": list(NEEDS_LIVE_CALLS),
        "failures": [
            {
                "id": r.scenario.id,
                "source": r.scenario.source,
                "error": r.error,
                "failures": [
                    {
                        "where": f.where,
                        "key": f.key,
                        "expected": f.expected,
                        "actual": f.actual,
                    }
                    for f in r.failures
                ],
            }
            for r in report.results
            if r.ran and not r.passed
        ],
    }
