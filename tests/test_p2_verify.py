"""Unit tests for the P2 verification tool (plan §12: "the verification tool
(equivalence edge cases)"). Covers both objective checks that feed `verify()`:
direct symbolic equivalence and substitution back into the problem's own
equation.
"""

from __future__ import annotations

from lanes.p2_verify import check_equivalence, verify, verify_by_substitution


def test_check_equivalence_handles_commutative_reordering():
    assert check_equivalence("x+1", "1+x") is True


def test_check_equivalence_handles_expanded_polynomial_forms():
    assert check_equivalence("x^2+2x+1", "(x+1)^2") is True


def test_check_equivalence_rejects_genuinely_different_expressions():
    assert check_equivalence("x^2+1", "x^2+2x+1") is False


def test_check_equivalence_handles_implicit_multiplication():
    # "2x" (no explicit "*") is exactly the notation these rubrics use --
    # regressing this silently degrades every math problem with a linear
    # term back to naive string comparison.
    assert check_equivalence("2x+4", "4+2x") is True


def test_check_equivalence_is_false_on_blank_input():
    assert check_equivalence("", "x+1") is False
    assert check_equivalence("x+1", "") is False


def test_check_equivalence_is_false_on_unparseable_input_not_a_crash():
    # "x = 2" isn't a bare expression sympy can sympify; the tool must
    # degrade to "not confirmed," never raise.
    assert check_equivalence("x = 2", "x = 2 ") is True  # exact-string fast path
    assert check_equivalence("x = 2", "2 = x") is False  # sympy can't parse either side


def test_verify_by_substitution_confirms_a_bare_value_answer():
    # The reference answer is "x = 2" but the student just wrote "2" -- a
    # direct string/symbolic comparison of "2" against "x = 2" can't succeed,
    # but substituting x=2 into the original equation proves it's correct.
    result = verify_by_substitution("2", "Solve for x:  2x + 6 = 10.")
    assert result is True


def test_verify_by_substitution_confirms_a_variable_prefixed_answer():
    result = verify_by_substitution("x = 2", "Solve for x:  2x + 6 = 10.")
    assert result is True


def test_verify_by_substitution_rejects_a_wrong_value():
    result = verify_by_substitution("5", "Solve for x:  2x + 6 = 10.")
    assert result is False


def test_verify_by_substitution_is_none_when_statement_has_no_bare_equation():
    # An expansion problem has no "lhs = rhs" to solve -- the tool should say
    # "not applicable," not guess.
    assert verify_by_substitution("x^2 + 2x + 1", "Expand and simplify:  (x + 1)^2.") is None


def test_verify_by_substitution_is_none_on_multi_variable_ambiguity():
    assert verify_by_substitution("3", "Solve: x + y = 10.") is None


def test_verify_prefers_direct_equivalence_and_falls_back_to_substitution():
    # Direct equivalence succeeds on the canonical case.
    assert verify("x = 2", "x = 2", "Solve for x:  2x + 6 = 10.") is True
    # Direct equivalence can't match "2" against "x = 2", but substitution
    # against the problem's own equation confirms it anyway.
    assert verify("2", "x = 2", "Solve for x:  2x + 6 = 10.") is True
    # A genuinely wrong answer is rejected by both checks.
    assert verify("5", "x = 2", "Solve for x:  2x + 6 = 10.") is False


def test_verify_works_without_a_reference_answer_via_substitution_alone():
    assert verify("2", "", "Solve for x:  2x + 6 = 10.") is True
    assert verify("5", "", "Solve for x:  2x + 6 = 10.") is False
