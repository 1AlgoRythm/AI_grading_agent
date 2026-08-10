"""[P3] Grounded feedback and student follow-up explanations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from contracts import Grade, ProblemGrade, ProblemOutcome, Rubric
from lanes.p3_review import finalize
from model_provider import call_model

__all__ = [
    "answer_followup",
    "clear_feedback_contexts",
    "finalize",
    "generate_feedback",
    "register_feedback_context",
]


_ESCALATION_NOTE = re.compile(
    r"\s*Escalated to human review after unresolved critic disagreement\.?\s*$"
)


def _clean(text: str | None) -> str:
    # Strips the internal-process note p2_engine.py appends to `evidence` on
    # escalation -- that's a routing signal for the human review queue, not
    # something a student should ever read as part of their feedback.
    text = re.sub(r"\s+", " ", text or "").strip()
    return _ESCALATION_NOTE.sub("", text).strip()


def _feedback_for_problem(problem_grade: ProblemGrade) -> str:
    score = f"{problem_grade.points_awarded:g}/{problem_grade.points_possible:g}"

    if problem_grade.outcome is ProblemOutcome.NO_ANSWER:
        return f"Score: {score}. No answer was provided."
    if problem_grade.outcome is ProblemOutcome.UNGRADEABLE:
        return (
            f"Score: {score}. The submitted response could not be graded. "
            "Please ask the instructor to review it."
        )

    if problem_grade.points_awarded == problem_grade.points_possible:
        result = "Full credit."
    else:
        reason = _clean(problem_grade.partial_credit_reason)
        result = f"Partial credit. {reason}" if reason else "Partial credit."

    evidence = _clean(problem_grade.evidence)
    evidence_sentence = f" {evidence}" if evidence else ""
    return f"Score: {score}. {result}{evidence_sentence}"


def generate_feedback(grade: Grade, rubric: Rubric) -> dict[UUID, str]:
    """Produce one score- and evidence-grounded explanation per problem.

    `rubric` is accepted (and validated against) rather than dropped, since
    every caller already has both and passing a grade/rubric pair from two
    different assignments is exactly the kind of mismatch worth catching
    here instead of silently producing feedback for the wrong criteria.
    """
    if grade.assignment_id != rubric.assignment_id:
        raise ValueError("grade and rubric must belong to the same assignment")
    return {
        item.problem_id: _feedback_for_problem(item)
        for item in grade.problem_grades
    }


@dataclass(frozen=True)
class FeedbackContext:
    grade: Grade
    rubric: Rubric


_FEEDBACK_CONTEXTS: dict[UUID, FeedbackContext] = {}


def register_feedback_context(grade: Grade, rubric: Rubric) -> None:
    """Register grounded context for the contract's submission-id chat seam."""
    if grade.assignment_id != rubric.assignment_id:
        raise ValueError("grade and rubric must belong to the same assignment")
    _FEEDBACK_CONTEXTS[grade.submission_id] = FeedbackContext(
        grade=grade.model_copy(deep=True), rubric=rubric.model_copy(deep=True)
    )


def clear_feedback_contexts() -> None:
    _FEEDBACK_CONTEXTS.clear()


def answer_followup(question: str, submission_id: UUID) -> str:
    """Answer a student's follow-up question, grounded in the recorded grade.

    The grounding block is generate_feedback's own per-problem output --
    the same evidence, rubric criteria, and score already shown on the grade
    panel -- fed to the model with an explicit instruction to answer from it
    alone and say so plainly when a question isn't covered by it, rather
    than freelancing an answer the recorded grade doesn't actually support.
    Fails closed: with no registered context, this returns the "not
    available yet" message without ever calling the model.
    """
    question = _clean(question)
    if not question:
        raise ValueError("question must not be blank")
    context = _FEEDBACK_CONTEXTS.get(submission_id)
    if context is None:
        return (
            "I can’t answer that yet because the approved grade and rubric are "
            "not available for this submission. Please ask the instructor."
        )

    feedback = generate_feedback(context.grade, context.rubric)
    grounding = "\n".join(
        f"Problem {problem_id.hex[-2:]}: {text}" for problem_id, text in feedback.items()
    )
    prompt = (
        "You are answering a student's question about their graded assignment. "
        "Answer using ONLY the recorded grading evidence below -- do not invent "
        "reasoning, scores, or rubric criteria that aren't in it. If the question "
        "asks about something this evidence doesn't cover (for example, how a "
        "problem *should* have been solved, when only the grade on what they "
        "actually submitted is recorded), say plainly that it isn't covered in "
        "the recorded grade instead of guessing.\n\n"
        f"Recorded grading evidence:\n{grounding}\n\n"
        f"Student question: {question}"
    )
    return call_model(prompt, max_tokens=400).strip()
