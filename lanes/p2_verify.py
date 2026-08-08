"""P2 verification helpers.

This module contains answer normalization and equivalence checking logic that
can evolve independently from the public `grade()` seam. Two independent
objective checks feed `verify()` (contracts.py: "[e.g. substitution]"):

- `check_equivalence` — direct symbolic comparison of the student's final
  answer against the curated reference answer.
- `verify_by_substitution` — a second, reference-answer-free check: pull the
  single-variable equation out of the problem statement, substitute the
  student's solved value back in, and confirm both sides balance. This
  catches answers that are objectively correct but formatted differently
  from the reference (e.g. a bare "2" where the reference says "x = 2").
"""

from __future__ import annotations

import re
from typing import Optional

__all__ = ["check_equivalence", "verify", "verify_by_substitution"]


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


def _extract_equation(statement: str) -> Optional[tuple[str, str]]:
    """Pull a bare 'lhs = rhs' equation out of a problem statement, e.g.
    "Solve for x:  2x + 6 = 10." -> ("2x + 6", "10"). Returns None when the
    statement doesn't look like exactly one single equation -- this tool
    only handles the unambiguous case, not free-text word problems."""
    if not statement or statement.count("=") != 1:
        return None
    text = statement.rsplit(":", 1)[-1] if ":" in statement else statement
    if "=" not in text:
        return None
    lhs, rhs = text.split("=", 1)
    lhs, rhs = lhs.strip().rstrip("."), rhs.strip().rstrip(".")
    return (lhs, rhs) if lhs and rhs else None


def _extract_assignment(final_answer: str) -> Optional[tuple[Optional[str], str]]:
    """Parse a student's final answer into (variable_name_or_None, value).
    Handles both "x = 2" and a bare "2" (variable name unknown, resolved
    later from the equation's own free symbol when there's exactly one)."""
    if not final_answer or not final_answer.strip():
        return None
    match = re.match(r"^\s*([a-zA-Z][a-zA-Z0-9]*)\s*=\s*(.+?)\s*$", final_answer)
    if match:
        return match.group(1), match.group(2)
    return None, final_answer.strip()


def verify_by_substitution(final_answer: str, problem_statement: str) -> Optional[bool]:
    """Best-effort objective check: substitute the student's solved value
    back into the equation from the problem statement and confirm both sides
    are equal.

    Returns `None` (not applicable) rather than `False` when the statement or
    answer doesn't look like a single-variable equation -- this is a
    confirmation path, not a rejection path, so callers should treat `None`
    as "this tool has no opinion," not "incorrect."
    """
    equation = _extract_equation(problem_statement or "")
    if equation is None:
        return None
    assignment = _extract_assignment(final_answer or "")
    if assignment is None:
        return None
    var_name, value_text = assignment

    try:
        import sympy as sp

        lhs_expr = _parse_expr(equation[0])
        rhs_expr = _parse_expr(equation[1])
        value_expr = _parse_expr(value_text)

        free_symbols = lhs_expr.free_symbols | rhs_expr.free_symbols
        if var_name:
            matches = [s for s in free_symbols if s.name == var_name]
            symbol = matches[0] if matches else sp.Symbol(var_name)
        elif len(free_symbols) == 1:
            symbol = next(iter(free_symbols))
        else:
            return None  # ambiguous: more than one unknown, can't substitute safely

        lhs_value = lhs_expr.subs(symbol, value_expr)
        rhs_value = rhs_expr.subs(symbol, value_expr)
        return bool(sp.simplify(lhs_value - rhs_value) == 0)
    except Exception:
        return None


def verify(answer: str, reference_answer: str, problem_statement: Optional[str] = None) -> bool:
    """Verify a student final answer, objectively, two ways: direct
    equivalence against the curated reference, or (when that doesn't confirm
    it) substitution back into the problem's own equation."""
    if reference_answer and check_equivalence(answer, reference_answer):
        return True
    if problem_statement:
        confirmed = verify_by_substitution(answer, problem_statement)
        if confirmed:
            return True
    return False
