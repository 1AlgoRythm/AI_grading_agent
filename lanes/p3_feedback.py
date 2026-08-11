"""[P3] Grounded feedback and student follow-up explanations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from contracts import Assignment, Grade, ProblemGrade, ProblemOutcome, Rubric, problem_label_map
from lanes.p3_review import finalize
from model_provider import call_model

__all__ = [
    "answer_followup",
    "clear_feedback_contexts",
    "feedback_history",
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
        if reason.startswith("Placeholder score:"):
            # p2_grader.py's offline fallback for answer types with no
            # objective check writes this when no real judgment was made
            # (no BYOK model configured, or its response didn't parse) --
            # useful for a reviewer deciding whether to trust the score, but
            # "no BYOK model is configured" is internal-process language
            # that has no business in student-facing feedback.
            reason = "This response could not be automatically evaluated in detail and will be reviewed by a human."
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


def _criterion_field(criterion, field: str, default: str = "") -> str:
    """Criteria arrive as RubricCriterion objects or plain dicts depending on
    the caller -- the same dual access lanes/p1_context.py's build_context
    already uses."""
    value = criterion.get(field) if isinstance(criterion, dict) else getattr(criterion, field, None)
    return str(value) if value not in (None, "") else default


def _truncate(text: str | None, limit: int) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


@dataclass(frozen=True)
class FeedbackContext:
    grade: Grade
    rubric: Rubric
    assignment: Assignment | None
    labels: dict[UUID, str]


_FEEDBACK_CONTEXTS: dict[UUID, FeedbackContext] = {}
# Bounded per-submission transcript. A follow-up chat where every turn is
# turn one cannot resolve "explain that differently" -- the pronoun has no
# referent. The recorded grade stays the ground truth; this only supplies
# conversational continuity, and it is never used as evidence.
_FEEDBACK_HISTORY: dict[UUID, list[tuple[str, str]]] = {}

_MAX_HISTORY_TURNS = 6
_MAX_STORED_TURNS = 20


def register_feedback_context(
    grade: Grade, rubric: Rubric, assignment: Assignment | None = None
) -> None:
    """Register grounded context for the contract's submission-id chat seam.

    `assignment` is optional (defaulted to None so every existing caller is
    unaffected) but strongly wanted: without it the chat sees only scores
    and evidence, so it cannot answer "what was the question?" or "how
    should I have solved it?" -- the two things students actually ask. With
    it, the chat is grounded in the same approved statement, reference
    solution, and rubric that build_context handed the grader.
    """
    if grade.assignment_id != rubric.assignment_id:
        raise ValueError("grade and rubric must belong to the same assignment")
    if assignment is not None and assignment.id != grade.assignment_id:
        raise ValueError("assignment does not match the grade's assignment")

    previous = _FEEDBACK_CONTEXTS.get(grade.submission_id)
    # Streamlit re-runs render() on every interaction, so resetting
    # unconditionally would wipe the transcript on every turn. Only a
    # genuinely different grade starts a new conversation.
    if previous is not None and previous.grade.id != grade.id:
        _FEEDBACK_HISTORY.pop(grade.submission_id, None)

    _FEEDBACK_CONTEXTS[grade.submission_id] = FeedbackContext(
        grade=grade.model_copy(deep=True),
        rubric=rubric.model_copy(deep=True),
        assignment=assignment.model_copy(deep=True) if assignment else None,
        labels=problem_label_map(assignment) if assignment else {},
    )


def feedback_history(submission_id: UUID) -> list[tuple[str, str]]:
    """(question, answer) pairs so far, oldest first. For rendering."""
    return list(_FEEDBACK_HISTORY.get(submission_id, ()))


def clear_feedback_contexts() -> None:
    _FEEDBACK_CONTEXTS.clear()
    _FEEDBACK_HISTORY.clear()


def _grounding_block(context: FeedbackContext) -> str:
    """The same material that produced the grade: statement, approved
    reference, rubric criteria, and the recorded score/evidence."""
    feedback = generate_feedback(context.grade, context.rubric)
    problems = {p.id: p for p in (context.assignment.problems if context.assignment else [])}

    lines: list[str] = []
    for item in context.grade.problem_grades:
        label = context.labels.get(item.problem_id) or item.problem_id.hex[-2:]
        lines.append(f"--- Problem {label} ---")

        problem = problems.get(item.problem_id)
        if problem is not None:
            lines.append(f"Question: {_clean(problem.statement)}")
            if problem.reference_answer:
                lines.append(f"Approved reference answer: {_clean(problem.reference_answer)}")
            if problem.reference_solution:
                lines.append(f"Approved reference solution: {_truncate(problem.reference_solution, 700)}")

        for criterion in context.rubric.for_problem(item.problem_id) or []:
            name = _criterion_field(criterion, "name", "criterion")
            points = _criterion_field(criterion, "points", "?")
            description = _criterion_field(criterion, "description")
            if description:
                lines.append(f"Rubric — {name} ({points} pts): {description}")

        lines.append(f"Grade recorded: {feedback[item.problem_id]}")
        lines.append("")
    return "\n".join(lines).strip()


def answer_followup(question: str, submission_id: UUID, method_context: Optional[str] = None) -> str:
    """Answer a student's follow-up, grounded in the recorded grade, the
    approved solution and rubric (when registered with an assignment), and
    the conversation so far.

    `method_context` is optional (defaulted to None so every existing
    caller is unaffected) -- a textbook snippet the CALLER already
    retrieved (e.g. via lanes/p1_rag.py's retrieve_method_from_textbook,
    queried on the student's actual question). Retrieval intentionally does
    not happen in here: every other lane function that consumes retrieved
    material (develop_solution, draft_rubric) receives it as a parameter
    rather than fetching it itself, and calling a chroma-backed lookup
    inside a "pure" answer function would make every test here silently
    depend on and cold-start against whatever happens to be in the real
    textbook/ folder.

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

    history = _FEEDBACK_HISTORY.setdefault(submission_id, [])
    transcript = ""
    if history:
        recent = "\n".join(f"Student: {q}\nYou: {a}" for q, a in history[-_MAX_HISTORY_TURNS:])
        transcript = f"\nEarlier in this conversation:\n{recent}\n"

    prompt = (
        "You are explaining a graded assignment to the student who submitted "
        "it. Be direct and encouraging.\n\n"
        "Answer using ONLY the recorded grading evidence below -- do not "
        "invent reasoning, scores, or rubric criteria that aren't in it. If "
        "the question asks about something this evidence doesn't cover, say "
        "plainly that it isn't covered in the recorded grade instead of "
        "guessing.\n\n"
        "If the student asks how a problem should have been solved, the "
        "approved reference solution below (when included) IS part of the "
        "recorded evidence -- use it rather than saying that's not covered. "
        "Never change a score; scores are set by the instructor, not by "
        "this chat.\n\n"
    )
    if method_context:
        # Grounding, not copy-paste, same as p1_solution.py's solution/
        # rubric prompts: a coarse relevance-gated match against the
        # question, not guaranteed to actually fit -- must be ignorable, and
        # even when relevant must never be quoted verbatim into what the
        # student reads. Scoped to conceptual explanation only; it never
        # supersedes the recorded grading evidence above.
        prompt += (
            f"Course material retrieved as reference grounding for this question:\n{method_context}\n"
            "Use it only if it is actually relevant to explaining the concept the "
            "student is asking about; if it is not relevant, ignore it entirely. "
            "Never quote or copy this material verbatim -- explain in your own "
            "words. It never changes the recorded score or evidence above.\n\n"
        )
    prompt += (
        f"=== Recorded grading evidence for this submission ===\n{_grounding_block(context)}\n"
        f"{transcript}\n"
        f"Student question: {question}"
    )
    answer = call_model(prompt, max_tokens=500).strip()
    history.append((question, answer))
    del history[:-_MAX_STORED_TURNS]
    return answer
