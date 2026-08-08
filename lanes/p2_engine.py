"""Core P2 grading engine.

This module keeps the internal grading flow separate from the public facade.
"""

from __future__ import annotations

import time
from typing import Optional

from contracts import (
    ArtifactStatus,
    Grade,
    GradingContext,
    Problem,
    ProblemGrade,
    ProblemOutcome,
    Rubric,
    Step,
    StepKind,
    StopReason,
    Submission,
    SubmissionContext,
    Trace,
)
from lanes.p2_heuristics import _critic_disagreement_step, _partial_credit_reason
from lanes.p2_verify import verify


def grade_submission(submission: Submission, rubric: Rubric, context: SubmissionContext) -> tuple[Grade, Trace]:
    problem_grades: list[ProblemGrade] = []
    steps: list[Step] = []
    start = time.perf_counter()

    for ans in submission.answers:
        ctx = context.context_for(ans.problem_id)
        possible = ctx.points_possible if ctx else 0.0
        final = (ans.final_answer or "").strip()
        reference = ctx.reference_answer if ctx else None
        matched = verify(final, reference) if final and reference else False

        steps.append(Step(type=StepKind.ACT, data={
            "tool": "verify",
            "problem": ans.problem_id.hex[-2:],
            "final_answer": final,
            "reference": reference,
            "matched": matched,
        }))

        partial_reason = None
        if ctx and not matched and final:
            partial_reason = _partial_credit_reason(final, ctx)
            if partial_reason:
                steps.append(Step(type=StepKind.REASON, data={
                    "problem": ans.problem_id.hex[-2:],
                    "reason": partial_reason,
                }))

        critic_agreement = None
        if final:
            critic_agreement = _critic_disagreement_step(matched, partial_reason)
            steps.append(Step(type=StepKind.CRITIQUE, data={
                "problem": ans.problem_id.hex[-2:],
                "critic_agreement": critic_agreement,
            }))

        if not final:
            problem_grades.append(ProblemGrade(
                problem_id=ans.problem_id,
                outcome=ProblemOutcome.NO_ANSWER,
                points_awarded=0,
                points_possible=possible,
                answer_matched=None,
                evidence="No final answer was provided.",
                critic_agreement=None,
            ))
        elif matched:
            problem_grades.append(ProblemGrade(
                problem_id=ans.problem_id,
                outcome=ProblemOutcome.GRADED,
                points_awarded=possible,
                points_possible=possible,
                answer_matched=True,
                evidence="Final answer matches the reference.",
                critic_agreement=critic_agreement,
            ))
        else:
            awarded = possible / 2 if partial_reason else 0.0
            problem_grades.append(ProblemGrade(
                problem_id=ans.problem_id,
                outcome=ProblemOutcome.GRADED,
                points_awarded=awarded,
                points_possible=possible,
                answer_matched=False,
                partial_credit_reason=(
                    partial_reason or
                    "Answer differs from the reference; no partial credit evidence was found."
                ),
                evidence=(
                    "Partial credit was awarded based on rubric evidence."
                    if partial_reason else
                    "Final answer differs from the reference."
                ),
                critic_agreement=critic_agreement,
            ))

    total_critic = [pg.critic_agreement for pg in problem_grades if pg.critic_agreement is not None]
    trace = Trace(
        stop_reason=StopReason.COMPLETED,
        critic_agreement=all(total_critic) if total_critic else None,
        num_revisions=0,
        tokens_used=sum(ctx.estimated_tokens for ctx in context.problem_contexts) if context.problem_contexts else None,
        latency_ms=int((time.perf_counter() - start) * 1000),
        steps=steps,
    )

    grade_obj = Grade(
        submission_id=submission.id,
        assignment_id=submission.assignment_id,
        problem_grades=problem_grades,
        status=ArtifactStatus.PROPOSED,
    )
    return grade_obj, trace
