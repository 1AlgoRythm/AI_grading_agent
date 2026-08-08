"""[P1] Facade for ingestion, retrieval, solution drafting, and context.

This module keeps the frozen P1 public API stable while delegating the actual
implementations to the dedicated lane modules:

- [lanes/p1_io.py](lanes/p1_io.py) for assignment and submission parsing.
- [lanes/p1_rag.py](lanes/p1_rag.py) for textbook retrieval.
- [lanes/p1_solution.py](lanes/p1_solution.py) for solution and rubric drafting.
- [lanes/p1_context.py](lanes/p1_context.py) for context assembly.

The split mirrors P3's module layout: one lane-facing facade, dedicated helper
files underneath, and conservative offline fallbacks so tests and the walking
skeleton remain deterministic.
"""

from __future__ import annotations

# Re-exported public API — implementations delegated to dedicated modules.
from lanes.p1_io import ingest_assignment as ingest_assignment
from lanes.p1_io import ingest_submission as ingest_submission
from lanes.p1_rag import retrieve_method_from_textbook as retrieve_method
from lanes.p1_solution import develop_solution as develop_solution
from lanes.p1_solution import draft_rubric as draft_rubric
from lanes.p1_context import build_context as build_context
from lanes.p1_context import build_submission_context as build_submission_context

__all__ = [
    "retrieve_method",
    "ingest_assignment",
    "develop_solution",
    "draft_rubric",
    "ingest_submission",
    "build_context",
    "build_submission_context",
]
