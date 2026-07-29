"""[P3] Feedback, human review & evaluation.

SKELETON stubs: feedback is a plain template built from the grade's evidence; the
chat is a placeholder; finalize performs the human approval action. Replace the
feedback/chat bodies with grounded generation and agentic RAG.
"""

from __future__ import annotations

from uuid import UUID

from contracts import ArtifactStatus, Grade, GradeResolution, Rubric, now


def generate_feedback(grade: Grade, rubric: Rubric) -> dict[UUID, str]:
    """SKELETON: template feedback per problem from the grade evidence. Real P3
    generates grounded, student-facing explanations."""
    feedback: dict[UUID, str] = {}
    for pg in grade.problem_grades:
        criteria = rubric.for_problem(pg.problem_id)
        name = criteria[0].name if criteria else "This problem"
        if pg.outcome is not pg.outcome.GRADED:
            feedback[pg.problem_id] = f"{name}: {pg.outcome.value.replace('_', ' ')}."
        elif pg.points_awarded == pg.points_possible:
            feedback[pg.problem_id] = (
                f"{name}: full marks ({pg.points_awarded}/{pg.points_possible}). {pg.evidence}"
            )
        else:
            feedback[pg.problem_id] = (
                f"{name}: {pg.points_awarded}/{pg.points_possible}. {pg.partial_credit_reason}"
            )
    return feedback


def answer_followup(question: str, submission_id: UUID) -> str:
    """SKELETON: placeholder chat reply. Real P3 does agentic RAG over the
    submission, rubric, and grade (and may cite the textbook)."""
    return (
        "[skeleton feedback chat] Once implemented, I'll explain any problem's "
        f"score for submission {submission_id.hex[-4:]}. You asked: {question!r}"
    )


def finalize(grade: Grade, approver_id: str) -> Grade:
    """Human review action: approve the grade. (An override would edit the
    problem scores first, then set resolution=HUMAN_OVERRIDDEN.)

    Order matters: the 'approved grade needs an approver' rule re-runs on every
    field assignment (validate_assignment=True), so set approver_id FIRST and
    flip status to APPROVED LAST — once its precondition is already in place.
    """
    grade.approver_id = approver_id
    grade.resolution = GradeResolution.HUMAN_CONFIRMED
    grade.approved_at = now()
    grade.status = ArtifactStatus.APPROVED   # set LAST, after approver_id exists
    return grade