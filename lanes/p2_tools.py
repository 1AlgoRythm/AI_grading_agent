"""[P2] The type-specific verification seam (plan §4).

The grader, critic, rubric, context engineering, feedback, and human loop are
all type-agnostic. This module is the one place that isn't: it maps an
assignment type to the objective check available for it.

The verdict is deliberately three-state:
    True  -- objectively confirmed correct
    False -- objectively confirmed wrong
    None  -- no objective check applies (prose, proof, free-form argument)

Collapsing None into False was the bug this module exists to prevent: it
records a correct proof as wrong, tells the grader so before it reasons, and
poisons the answer-match-rate metric P3 reports.
"""
from __future__ import annotations

import re
from typing import Optional, Protocol

from lanes.p2_verify import check_equivalence

__all__ = ["VerificationTool", "MathVerifier", "ProseVerifier", "get_verifier", "verify_verdict"]


def _looks_symbolic(text: str) -> bool:
    """A short symbolic expression equivalence checking can compare, versus
    free-form prose it cannot. Mirrors p1_solution._looks_symbolic on purpose:
    the same judgment, applied at grading time instead of solution-review time."""
    stripped = (text or "").strip()
    if not stripped or len(stripped.split()) > 12:
        return False
    return bool(re.search(r"[0-9=+\-*/^]", stripped))


class VerificationTool(Protocol):
    name: str

    def verify(self, answer: str, reference_answer: str, problem_statement: str) -> Optional[bool]:
        ...


class MathVerifier:
    """SymPy-backed. Returns None rather than False whenever the comparison
    could not meaningfully run -- an unparseable answer is not a wrong answer.

    Only does one thing: symbolic equivalence against the curated reference
    answer. A previous version also tried substituting the student's value
    back into an equation regex-extracted from the raw problem statement --
    removed because that only ever worked for the narrowest textbook phrasing
    ("Solve for x: ...") and gave false confidence on anything else.
    Assignments aren't fixed to that shape, so anything equivalence can't
    confirm is left as "not applicable" for the grader/critic LLM to judge.
    """

    name = "sympy_math"

    def verify(self, answer: str, reference_answer: str, problem_statement: str) -> Optional[bool]:
        answer = (answer or "").strip()
        if not answer:
            return None

        if reference_answer and _looks_symbolic(reference_answer) and _looks_symbolic(answer):
            return check_equivalence(answer, reference_answer)

        # Reference or answer is prose (or there's no reference at all):
        # equivalence checking has no opinion here.
        return None


class ProseVerifier:
    """Short answer, proofs, algorithm arguments. There is no objective check,
    and saying so honestly is the point -- it routes the grade to the critic
    and the human gate instead of fabricating a verdict."""

    name = "none_prose"

    def verify(self, answer: str, reference_answer: str, problem_statement: str) -> Optional[bool]:
        return None


_VERIFIERS: dict[str, VerificationTool] = {
    "math": MathVerifier(),
    "short_answer": ProseVerifier(),
    "proof": ProseVerifier(),
}


def get_verifier(assignment_type: str) -> VerificationTool:
    return _VERIFIERS.get((assignment_type or "math").strip().lower(), ProseVerifier())


def verify_verdict(
    answer: str,
    reference_answer: str,
    problem_statement: str,
    assignment_type: str = "math",
) -> Optional[bool]:
    return get_verifier(assignment_type).verify(answer, reference_answer, problem_statement)
