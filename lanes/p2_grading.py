"""[P2] Grading agent + critic + verification tool.

This facade exposes the stable public seam while delegating the grading
implementation to dedicated helper modules.
"""

from __future__ import annotations

from contracts import Grade, Rubric, Submission, SubmissionContext, Trace
from lanes.p2_batch import (
    BatchResult,
    check_critic_independence,
    critic_agreement_rate,
    grade_batch,
    grade_batch_async,
    review_queue_order,
)
from lanes.p2_engine import grade_submission

__all__ = [
    "grade",
    "grade_batch",
    "grade_batch_async",
    "review_queue_order",
    "critic_agreement_rate",
    "check_critic_independence",
    "BatchResult",
]


def grade(
    submission: Submission,
    rubric: Rubric,
    context: SubmissionContext,
    assignment_type: str = "math",
) -> tuple[Grade, Trace]:
    return grade_submission(submission, rubric, context, assignment_type)
