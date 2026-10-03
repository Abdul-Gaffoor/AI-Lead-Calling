"""Telugu and English evaluation harness (MVP sections 37 and 38).

The MVP puts a gate in front of calling real customers: a scripted corpus
that has to pass first. This package is that gate — `corpus` loads the
scenarios, `runner` plays them through the real conversation orchestrator,
and `report` turns the results into the section 38 measures.
"""

from backend.evaluation.corpus import CorpusError, Scenario, load
from backend.evaluation.report import build, render, to_dict
from backend.evaluation.runner import Result, run

__all__ = [
    "CorpusError",
    "Result",
    "Scenario",
    "build",
    "load",
    "render",
    "run",
    "to_dict",
]
