"""
Shared fixtures — one realistic math assignment wired end to end.

Every lane builds and tests against these on day one. IDs are fixed constants so
related objects cross-reference correctly (e.g. sample_grade().submission_id ==
sample_submission().id). Each function returns a FRESH instance, so tests can
mutate freely without leaking state between them.

The example deliberately exercises the tricky paths:
  * Q1 — fully correct  -> full credit, answer_matched=True
  * Q2 — dropped a term  -> partial credit (forces partial_credit_reason)
"""

from __future__ import annotations

from uuid import UUID

from contracts import (
    Assignment,
    ArtifactStatus,
    Grade,
    GradeResolution,
    GradingContext,
    Problem,
    ProblemGrade,
    ProblemOutcome,
    Rubric,
    RubricCriterion,
    SolutionSource,
    Step,
    StepKind,
    StopReason,
    Submission,
    SubmissionAnswer,
    SubmissionContext,
    Trace,
    rough_token_estimate,
)

# --- fixed IDs so fixtures cross-reference cleanly ------------------------- #
AID = UUID("00000000-0000-0000-0000-0000000000a0")   # assignment
Q1 = UUID("00000000-0000-0000-0000-0000000000b1")    # problem 1
Q2 = UUID("00000000-0000-0000-0000-0000000000b2")    # problem 2
RID = UUID("00000000-0000-0000-0000-0000000000c0")   # rubric
C1 = UUID("00000000-0000-0000-0000-0000000000c1")    # criterion for Q1
C2 = UUID("00000000-0000-0000-0000-0000000000c2")    # criterion for Q2
SID = UUID("00000000-0000-0000-0000-0000000000d0")   # submission
GID = UUID("00000000-0000-0000-0000-0000000000e0")   # grade

GRADING_POLICY = (
    "You are grading undergraduate math. Grade generously: reward the correct "
    "method even when the final answer has a minor arithmetic slip, and give "
    "partial credit for partial progress. Do not deduct for notation or style. "
    "When the student's intent is clear, interpret in their favor."
)


# --------------------------------------------------------------------------- #
# Assignment (with approved solutions)
# --------------------------------------------------------------------------- #

def sample_assignment() -> Assignment:
    return Assignment(
        id=AID,
        label="hw3",
        title="Homework 3 — Linear Equations & Expansion",
        type="math",
        problems=[
            Problem(
                id=Q1,
                assignment_id=AID,
                label="Q1",
                statement="Solve for x:  2x + 6 = 10.",
                points_possible=5,
                reference_answer="x = 2",
                reference_solution="2x + 6 = 10 -> 2x = 4 -> x = 2.",
                solution_source=SolutionSource.GENERATED,
                solution_status=ArtifactStatus.APPROVED,
            ),
            Problem(
                id=Q2,
                assignment_id=AID,
                label="Q2",
                statement="Expand and simplify:  (x + 1)^2.",
                points_possible=5,
                reference_answer="x^2 + 2x + 1",
                reference_solution="(x+1)^2 = (x+1)(x+1) = x^2 + 2x + 1.",
                solution_source=SolutionSource.GENERATED,
                solution_status=ArtifactStatus.APPROVED,
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# Rubric (approved, lenient)
# --------------------------------------------------------------------------- #

def sample_rubric() -> Rubric:
    return Rubric(
        id=RID,
        assignment_id=AID,
        version=1,
        status=ArtifactStatus.APPROVED,
        criteria=[
            RubricCriterion(
                id=C1,
                problem_id=Q1,
                name="Correct isolation and solution",
                description=(
                    "Full credit for the correct value of x with a valid method. "
                    "Award most of the credit if the method is right but there is "
                    "a minor arithmetic slip."
                ),
                points=5,
                failure_signals=["no rearrangement shown", "wrong operation applied"],
            ),
            RubricCriterion(
                id=C2,
                problem_id=Q2,
                name="Correct expansion",
                description=(
                    "Full credit for x^2 + 2x + 1. Partial credit if the square is "
                    "attempted but a term is dropped or mis-added."
                ),
                points=5,
                failure_signals=["middle term 2x missing", "treated as x^2 + 1"],
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# Submission (Q1 correct, Q2 drops the middle term)
# --------------------------------------------------------------------------- #

def sample_submission() -> Submission:
    return Submission(
        id=SID,
        assignment_id=AID,
        student_label="student_07",
        sanitized=True,
        answers=[
            SubmissionAnswer(
                problem_id=Q1,
                work_text="2x + 6 = 10, so 2x = 4, therefore x = 2.",
                final_answer="x = 2",
            ),
            SubmissionAnswer(
                problem_id=Q2,
                work_text="(x + 1)^2 = x^2 + 1.",
                final_answer="x^2 + 1",
            ),
        ],
    )


# --------------------------------------------------------------------------- #
# Context (what P1 hands to P2) — budget-checked
# --------------------------------------------------------------------------- #
#
# `sample_submission_context()` is the handoff shape P1 guarantees to P2:
# one `GradingContext` per problem, already curated, budget-checked, and
# aligned with the approved rubric + solution artifacts.

def _context_for(problem: Problem, answer: SubmissionAnswer, criteria) -> GradingContext:
    parts = [
        problem.statement,
        problem.reference_solution or "",
        problem.reference_answer or "",
        GRADING_POLICY,
        answer.work_text,
        *(c.description for c in criteria),
    ]
    est = sum(rough_token_estimate(p) for p in parts)
    return GradingContext(
        problem_id=problem.id,
        problem_statement=problem.statement,
        reference_solution=problem.reference_solution or "",
        reference_answer=problem.reference_answer,
        rubric_criteria=list(criteria),
        grading_policy=GRADING_POLICY,
        student_work=answer.work_text,
        student_final_answer=answer.final_answer,
        points_possible=problem.points_possible,
        estimated_tokens=est,
    )


def sample_submission_context() -> SubmissionContext:
    """Return the canonical P1→P2 handoff fixture.

    P2 should be able to consume this object directly without needing to know
    how P1 assembled it.
    """
    assignment = sample_assignment()
    rubric = sample_rubric()
    submission = sample_submission()
    problems = {p.id: p for p in assignment.problems}
    contexts = [
        _context_for(problems[a.problem_id], a, rubric.for_problem(a.problem_id))
        for a in submission.answers
    ]
    return SubmissionContext(submission_id=submission.id, problem_contexts=contexts)


# --------------------------------------------------------------------------- #
# Grade + Trace (a plausible grader+critic result)
# --------------------------------------------------------------------------- #

def sample_grade() -> Grade:
    return Grade(
        id=GID,
        submission_id=SID,
        assignment_id=AID,
        status=ArtifactStatus.PROPOSED,
        escalated=False,
        resolution=GradeResolution.AI_ONLY,
        problem_grades=[
            ProblemGrade(
                problem_id=Q1,
                outcome=ProblemOutcome.GRADED,
                points_awarded=5,
                points_possible=5,
                answer_matched=True,
                evidence="Correct rearrangement and x = 2 matches the reference.",
                critic_agreement=True,
            ),
            ProblemGrade(
                problem_id=Q2,
                outcome=ProblemOutcome.GRADED,
                points_awarded=2.5,
                points_possible=5,
                answer_matched=False,
                partial_credit_reason=(
                    "Attempted the square but dropped the middle cross term 2x; "
                    "answer x^2 + 1 is incomplete. Method partially correct."
                ),
                evidence="Student wrote (x+1)^2 = x^2 + 1, missing 2x.",
                critic_agreement=True,
            ),
        ],
    )


def sample_trace() -> Trace:
    return Trace(
        stop_reason=StopReason.COMPLETED,
        critic_agreement=True,
        num_revisions=0,
        tokens_used=1840,
        latency_ms=5200,
        steps=[
            Step(type=StepKind.REASON, data={"note": "Check Q1 final answer with the tool."}),
            Step(type=StepKind.ACT, data={"tool": "check_equivalence", "args": ["x = 2", "x = 2"]}),
            Step(type=StepKind.OBSERVE, data={"result": True}),
            Step(type=StepKind.REASON, data={"note": "Q2 answer differs; inspect the work for partial credit."}),
            Step(type=StepKind.ACT, data={"tool": "check_equivalence", "args": ["x^2 + 1", "x^2 + 2x + 1"]}),
            Step(type=StepKind.OBSERVE, data={"result": False}),
            Step(type=StepKind.CRITIQUE, data={"agrees": True, "note": "Partial credit and reason look justified."}),
        ],
    )


if __name__ == "__main__":
    # Smoke test: build every fixture and print a one-line summary.
    a, r, s = sample_assignment(), sample_rubric(), sample_submission()
    ctx, g, t = sample_submission_context(), sample_grade(), sample_trace()
    assert ctx.submission_id == s.id
    assert g.submission_id == s.id
    print(f"assignment: {a.label} ({a.type}), {len(a.problems)} problems")
    print(f"rubric v{r.version} [{r.status.value}], {len(r.criteria)} criteria")
    print(f"submission: {s.student_label}, {len(s.answers)} answers")
    print(f"context: {len(ctx.problem_contexts)} problem contexts, budgets ok")
    print(f"grade: {g.total_awarded}/{g.total_possible} ({g.fraction:.0%}), "
          f"status={g.status.value}, escalated={g.escalated}")
    print(f"trace: stop={t.stop_reason.value}, steps={len(t.steps)}")
    print("all fixtures built and cross-referenced OK")