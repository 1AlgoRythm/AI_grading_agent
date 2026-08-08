"""P2 grading heuristics and partial credit helpers."""

from __future__ import annotations

from typing import Optional

from contracts import GradingContext


def _partial_credit_reason(final_answer: str, context: GradingContext) -> Optional[str]:
    if not final_answer:
        return None
    final = final_answer.strip().lower().replace(" ", "")
    work = (context.student_work or "").lower()
    reference = (context.reference_answer or "").strip().lower().replace(" ", "")

    missing_middle_term = (
        '2x' not in final and '2x' in reference and
        ('2x' in work or 'middle term' in work or 'square' in work)
    )
    if missing_middle_term:
        return (
            "Partial credit: the student attempted the correct expansion but dropped "
            "the middle 2x term."
        )

    for criterion in context.rubric_criteria:
        for signal in criterion.failure_signals:
            if signal.lower() in work:
                return (
                    f"Partial credit for meeting some rubric criteria. "
                    f"Detected failure signal: '{signal}'."
                )

    if 'square' in work or '^2' in work or 'expand' in work or 'solution' in work:
        return (
            "Partial credit: the student appears to have used the right method, "
            "but the final answer does not exactly match the reference."
        )

    return None


def _critic_disagreement_step(matched: bool, partial_reason: Optional[str]) -> bool:
    if matched:
        return True
    return False
