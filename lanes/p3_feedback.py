"""[P3] Grounded feedback and student follow-up explanations."""

from __future__ import annotations

import re
import os
from dataclasses import dataclass
from uuid import UUID

from contracts import Grade, ProblemGrade, ProblemOutcome, Rubric
from lanes.p3_review import finalize
from lanes.p1_rag import retrieve_method_from_textbook
from model_provider import call_model

__all__ = [
    "answer_followup",
    "clear_feedback_contexts",
    "finalize",
    "generate_feedback",
    "register_feedback_context",
]


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _criterion_summary(grade: ProblemGrade, rubric: Rubric) -> str:
    criteria = rubric.for_problem(grade.problem_id)
    if not criteria:
        return "the requirements for this problem"
    return "; ".join(
        f"{_clean(item.name)}: {_clean(item.description).rstrip('.')}" for item in criteria
    )


def _feedback_for_problem(problem_grade: ProblemGrade, rubric: Rubric) -> str:
    score = f"{problem_grade.points_awarded:g}/{problem_grade.points_possible:g}"
    criterion = _criterion_summary(problem_grade, rubric)
    evidence = _clean(problem_grade.evidence)

    if problem_grade.outcome is ProblemOutcome.NO_ANSWER:
        return f"Score: {score}. No answer was provided. Review {criterion}."
    if problem_grade.outcome is ProblemOutcome.UNGRADEABLE:
        return (
            f"Score: {score}. The submitted response could not be graded. "
            f"Please ask the instructor to review it. Relevant rubric: {criterion}."
        )

    if problem_grade.points_awarded == problem_grade.points_possible:
        result = f"Full credit. Your work met {criterion}."
        action = " Keep using this approach and show the key steps clearly."
    else:
        reason = _clean(problem_grade.partial_credit_reason)
        result = f"Partial credit. {reason or 'The response met only part of the rubric.'}"
        action = f" Next step: compare your work with {criterion}."

    evidence_sentence = f" Evidence: {evidence}" if evidence else ""
    return f"Score: {score}. {result}{evidence_sentence}{action}"


def generate_feedback(grade: Grade, rubric: Rubric) -> dict[UUID, str]:
    """Produce one rubric- and evidence-grounded explanation per problem."""
    if grade.assignment_id != rubric.assignment_id:
        raise ValueError("grade and rubric must belong to the same assignment")
    return {
        item.problem_id: _feedback_for_problem(item, rubric)
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
    """Answer only from registered grade/rubric context."""
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
    lowered = question.lower()
    selected = [
        (problem_id, text)
        for problem_id, text in feedback.items()
        if str(problem_id).lower() in lowered or problem_id.hex[-2:] in lowered
    ]
    if not selected:
        selected = list(feedback.items())
    explanations = " ".join(
        f"Problem {problem_id.hex[-2:]} — {text}" for problem_id, text in selected
    )
    rubric_query = " ".join(
        criterion.description for criterion in context.rubric.criteria
        if any(criterion.problem_id == problem_id for problem_id, _ in selected)
    )
    source = retrieve_method_from_textbook(f"{question} {rubric_query}")
    grounded = f"Based on the approved rubric and recorded grading evidence: {explanations}"
    if source:
        citation = _clean(source)[:350]
        grounded += f" Course-material citation: {citation}"
    if os.getenv("MODEL_PROVIDER") and os.getenv("MODEL_API_KEY"):
        prompt = (
            "Answer the student's question using only the supplied grading evidence, rubric, "
            "and course excerpt. Be specific, consistent with the score, and actionable. "
            "If an excerpt is present, cite it as Course material.\n"
            f"Question: {question}\nGrounded context: {grounded}"
        )
        return _clean(call_model(prompt, max_tokens=350, temperature=0.0))
    return grounded
