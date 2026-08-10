"""Core P2 grading engine.

Per problem, this runs the full agentic loop from the plan (§5):

  ACT       -- the SymPy-backed `verify` tool objectively checks the final
               answer (the only source of truth for that check).
  REASON    -- the grader agent (p2_grader) reasons over the rubric and
               shown work to assign lenient partial credit.
  CRITIQUE  -- an independent critic agent (p2_critic) adversarially reviews
               the proposed grade. Skipped on an objective tool match, since
               there is nothing subjective left to challenge (design guard 2).
  REVISION  -- bounded reconciliation: on disagreement the grader gets one
               revision round with the critique; if the critic still
               disagrees afterward, the submission's Grade is escalated for
               human review rather than looping further.

This module keeps the internal grading flow separate from the public facade.
"""

from __future__ import annotations

import time
from typing import Optional
from uuid import UUID

from contracts import (
    ArtifactStatus,
    Grade,
    GradingContext,
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
from lanes.p2_critic import run_critic
from lanes.p2_grader import run_grader
from lanes.p2_tools import verify_verdict


def _grade_one_problem(
    problem_id: UUID,
    final: str,
    ctx: Optional[GradingContext],
    possible: float,
    steps: list[Step],
    assignment_type: str = "math",
) -> tuple[ProblemGrade, int]:
    tag = problem_id.hex[-2:]

    if not final:
        return ProblemGrade(
            problem_id=problem_id,
            outcome=ProblemOutcome.NO_ANSWER,
            points_awarded=0,
            points_possible=possible,
            answer_matched=None,
            evidence="No final answer was provided.",
            critic_agreement=None,
        ), 0

    if ctx is None:
        return ProblemGrade(
            problem_id=problem_id,
            outcome=ProblemOutcome.UNGRADEABLE,
            points_awarded=0,
            points_possible=possible,
            answer_matched=None,
            evidence="No grading context was available for this problem.",
            critic_agreement=None,
        ), 0

    # A context that carries its own detected type (fix #7's per-problem
    # classifier) always wins over the submission-wide default -- that's
    # what makes a mixed assignment route each problem to its own verifier
    # instead of one type picked for the whole submission.
    effective_type = ctx.problem_type or assignment_type
    matched = verify_verdict(final, ctx.reference_answer or "", ctx.problem_statement, effective_type)
    steps.append(Step(type=StepKind.ACT, data={
        "tool": "verify",
        "problem": tag,
        "problem_id": str(problem_id),
        "final_answer": final,
        "reference": ctx.reference_answer,
        "matched": matched,
        "verdict": {True: "confirmed", False: "contradicted", None: "not_applicable"}[matched],
    }))

    result = run_grader(ctx, matched)
    steps.append(Step(type=StepKind.REASON, data={
        "problem": tag,
        "problem_id": str(problem_id),
        "points_awarded": result.points_awarded,
        "rationale": result.rationale,
    }))

    if matched is True:
        # Objective, tool-checked answer: nothing subjective to critique.
        critic_agrees: Optional[bool] = True
        revisions = 0
    else:
        critique = run_critic(ctx, result)
        steps.append(Step(type=StepKind.CRITIQUE, data={
            "problem": tag,
            "problem_id": str(problem_id),
            "agrees": critique.agrees,
            "critique": critique.critique,
        }))
        critic_agrees = critique.agrees
        revisions = 0
        if not critique.agrees:
            steps.append(Step(type=StepKind.REVISION, data={
                "problem": tag,
                "problem_id": str(problem_id),
                "critique": critique.critique,
            }))
            result = run_grader(ctx, matched, critique=critique.critique)
            revisions = 1
            recheck = run_critic(ctx, result)
            steps.append(Step(type=StepKind.CRITIQUE, data={
                "problem": tag,
                "problem_id": str(problem_id),
                "agrees": recheck.agrees,
                "critique": recheck.critique,
                "after_revision": True,
            }))
            critic_agrees = recheck.agrees

    escalate = critic_agrees is False
    evidence = result.evidence
    if escalate:
        evidence += " Escalated to human review after unresolved critic disagreement."

    problem_grade = ProblemGrade(
        problem_id=problem_id,
        outcome=ProblemOutcome.GRADED,
        points_awarded=result.points_awarded,
        points_possible=possible,
        answer_matched=matched,
        partial_credit_reason=result.partial_credit_reason,
        evidence=evidence,
        critic_agreement=critic_agrees,
    )
    return problem_grade, revisions


def grade_submission(
    submission: Submission,
    rubric: Rubric,
    context: SubmissionContext,
    assignment_type: str = "math",
) -> tuple[Grade, Trace]:
    problem_grades: list[ProblemGrade] = []
    steps: list[Step] = []
    start = time.perf_counter()
    total_revisions = 0

    for ans in submission.answers:
        ctx = context.context_for(ans.problem_id)
        possible = ctx.points_possible if ctx else 0.0
        final = (ans.final_answer or "").strip()

        problem_grade, revisions = _grade_one_problem(
            ans.problem_id, final, ctx, possible, steps, assignment_type
        )
        problem_grades.append(problem_grade)
        total_revisions += revisions

    critic_signals = [pg.critic_agreement for pg in problem_grades if pg.critic_agreement is not None]
    escalated = any(pg.critic_agreement is False for pg in problem_grades)

    trace = Trace(
        stop_reason=StopReason.ESCALATED if escalated else StopReason.COMPLETED,
        critic_agreement=all(critic_signals) if critic_signals else None,
        num_revisions=total_revisions,
        tokens_used=sum(c.estimated_tokens for c in context.problem_contexts) if context.problem_contexts else None,
        latency_ms=int((time.perf_counter() - start) * 1000),
        steps=steps,
    )

    grade_obj = Grade(
        submission_id=submission.id,
        assignment_id=submission.assignment_id,
        problem_grades=problem_grades,
        status=ArtifactStatus.PROPOSED,
        escalated=escalated,
    )
    return grade_obj, trace
