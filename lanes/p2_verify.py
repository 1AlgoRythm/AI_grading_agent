"""P2 verification helpers.

This module contains answer normalization and equivalence checking logic that
can evolve independently from the public `grade()` seam.

`check_equivalence` is the one objective tool: direct symbolic comparison of
the student's final answer against the curated reference answer. It's the
tool the plan names explicitly (`check_equivalence(student, reference)`) and
the one that actually generalizes across assignment types -- it works for
any assignment where a reference answer string exists to compare against,
regardless of the problem's shape.

A previous version of this module also had `verify_by_substitution`: a
regex-based "extract the one equation from the raw problem statement,
substitute the student's value back in, check it balances" heuristic. It was
removed because it only ever worked for the narrowest case (a single
textbook "solve for x" linear equation stated in exactly that form) and gave
false confidence nowhere else -- assignments aren't fixed to that shape.
Anything `check_equivalence` can't confirm is now left for the grader/critic
LLM calls to judge, rather than a hand-rolled parser pretending to be a
general-purpose math engine.
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["check_equivalence", "verify"]


def _normalize_answer(answer: str) -> str:
    normalized = answer.strip().lower()
    normalized = normalized.rstrip(".")
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("+-", "-").replace("-+", "-")
    normalized = re.sub(r"\s*\*\s*", "*", normalized)
    normalized = re.sub(r"\s*\+\s*", "+", normalized)
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    return normalized


def _parse_expr(text: str):
    import sympy as sp  # noqa: F401  (imported for callers' `except` clauses)
    from sympy.parsing.sympy_parser import (
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    # Rubric-style answers use implicit multiplication (e.g. "2x"), which
    # plain sympify()/eval-based parsing rejects as a syntax error; this
    # transformation set inserts the "*" that bare sympify() requires.
    transformations = standard_transformations + (implicit_multiplication_application,)
    return parse_expr(text.replace('^', '**'), transformations=transformations)


def check_equivalence(a: str, b: str) -> bool:
    """Compare two answers for semantic equivalence when possible."""
    if not a or not b:
        return False

    a_norm = _normalize_answer(a)
    b_norm = _normalize_answer(b)
    if a_norm == b_norm:
        return True

    try:
        import sympy as sp

        a_expr = _parse_expr(a)
        b_expr = _parse_expr(b)
        return sp.simplify(a_expr - b_expr) == 0
    except Exception:
        return False


def verify(answer: str, reference_answer: str, problem_statement: Optional[str] = None) -> bool:
    """Verify a student final answer against the curated reference via
    `check_equivalence`. `problem_statement` is accepted for signature
    stability but unused -- the objective check is the reference-answer
    comparison; anything it can't confirm is left for the LLM grader/critic
    to judge, not reconstructed from the raw problem text."""
    return bool(reference_answer) and check_equivalence(answer, reference_answer)
