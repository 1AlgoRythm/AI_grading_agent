"""[P2] Grading agent + critic + verification tool.

This facade exposes the stable public seam while delegating the grading
implementation to dedicated helper modules.
"""

from __future__ import annotations

from contracts import Grade, Rubric, Submission, SubmissionContext, Trace
from lanes.p2_engine import grade_submission


def grade(submission: Submission, rubric: Rubric, context: SubmissionContext) -> tuple[Grade, Trace]:
    return grade_submission(submission, rubric, context)
