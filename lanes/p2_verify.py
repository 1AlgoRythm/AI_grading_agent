"""P2 verification helpers.

This module contains answer normalization and equivalence checking logic that
can evolve independently from the public `grade()` seam.
"""

from __future__ import annotations

import re


def _normalize_answer(answer: str) -> str:
    normalized = answer.strip().lower()
    normalized = normalized.rstrip(".")
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("+-", "-").replace("-+", "-")
    normalized = re.sub(r"\s*\*\s*", "*", normalized)
    normalized = re.sub(r"\s*\+\s*", "+", normalized)
    normalized = re.sub(r"\s*-\s*", "-", normalized)
    return normalized


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

        a_expr = sp.sympify(a.replace('^', '**'), evaluate=True)
        b_expr = sp.sympify(b.replace('^', '**'), evaluate=True)
        return sp.simplify(a_expr - b_expr) == 0
    except Exception:
        return False


def verify(answer: str, reference_answer: str) -> bool:
    """Verify a student final answer against the reference answer."""
    return check_equivalence(answer, reference_answer or "")
