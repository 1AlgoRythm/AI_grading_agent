"""[P2] Grading agent + critic + verification tool.

SKELETON stubs: a trivial grader that awards full marks on an exact-ish answer
match and half marks otherwise. NO ReAct loop and NO critic yet — those are the
real work. Keep the signatures; replace the bodies.
"""

from __future__ import annotations

from contracts import (
    ArtifactStatus,
    Grade,
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


def check_equivalence(a: str, b: str) -> bool:
    """SKELETON: naive normalized string compare. Real P2 uses SymPy so that
    'x + 1' and '1 + x' are recognized as equal."""
    def norm(s: str) -> str:
        return s.replace(" ", "").replace("x=", "").replace("=", "").lower()
    return norm(a) == norm(b)


def verify(answer: str, problem: Problem) -> bool:
    """SKELETON: compare against the reference answer. Real P2 verifies by
    substitution where the type allows."""
    return check_equivalence(answer, problem.reference_answer or "")


def grade(submission: Submission, rubric: Rubric, context: SubmissionContext) -> tuple[Grade, Trace]:
    """SKELETON grader. Walks each answer, checks the final answer with the tool,
    and assigns full / partial / no-answer outcomes. Real P2 replaces this with
    the ReAct grader, per-criterion partial credit, and the independent critic."""
    problem_grades: list[ProblemGrade] = []
    steps: list[Step] = []

    for ans in submission.answers:
        ctx = context.context_for(ans.problem_id)
        possible = ctx.points_possible if ctx else 0.0
        reference = ctx.reference_answer if ctx else None
        final = (ans.final_answer or "").strip()
        matched = check_equivalence(final, reference or "") if (final and reference) else False

        steps.append(Step(type=StepKind.ACT, data={
            "tool": "check_equivalence",
            "problem": ans.problem_id.hex[-2:],
            "matched": matched,
        }))

        if not final:
            problem_grades.append(ProblemGrade(
                problem_id=ans.problem_id,
                outcome=ProblemOutcome.NO_ANSWER,
                points_awarded=0,
                points_possible=possible,
                answer_matched=None,
                evidence="No final answer was provided.",
            ))
        elif matched:
            problem_grades.append(ProblemGrade(
                problem_id=ans.problem_id,
                outcome=ProblemOutcome.GRADED,
                points_awarded=possible,
                points_possible=possible,
                answer_matched=True,
                evidence="Final answer matches the reference.",
                critic_agreement=None,   # no critic in the skeleton
            ))
        else:
            problem_grades.append(ProblemGrade(
                problem_id=ans.problem_id,
                outcome=ProblemOutcome.GRADED,
                points_awarded=possible / 2,
                points_possible=possible,
                answer_matched=False,
                partial_credit_reason=(
                    "SKELETON placeholder: answer differs from the reference; "
                    "replace with rubric-based partial credit."
                ),
                evidence="Final answer differs from the reference.",
                critic_agreement=None,
            ))

    grade_obj = Grade(
        submission_id=submission.id,
        assignment_id=submission.assignment_id,
        problem_grades=problem_grades,
        status=ArtifactStatus.PROPOSED,
    )
    trace = Trace(stop_reason=StopReason.COMPLETED, critic_agreement=None,
                  num_revisions=0, steps=steps)
    return grade_obj, trace
