"""Unit tests for the P2 verification tool (plan §12: "the verification tool
(equivalence edge cases)"). `check_equivalence` (direct symbolic comparison
against the curated reference answer) is the one objective check -- a
regex-based "extract the equation from the raw problem text and substitute"
heuristic used to live here too; it was removed because it only ever worked
for the narrowest textbook phrasing and gave false confidence on anything
else assignments actually look like. Anything equivalence can't confirm is
left for the LLM grader/critic to judge, not reconstructed by a parser.
"""

from __future__ import annotations

from lanes.p2_verify import check_equivalence, verify


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


def test_verify_confirms_via_direct_equivalence():
    assert verify("x = 2", "x = 2", "Solve for x:  2x + 6 = 10.") is True


def test_verify_rejects_when_equivalence_does_not_confirm():
    # "2" vs "x = 2" doesn't match by direct equivalence -- there's no more
    # substitution fallback to reconstruct the equation and rescue it. That's
    # intentional: whether that's genuinely wrong or just differently
    # formatted is exactly the judgment call left to the grader/critic LLM.
    assert verify("2", "x = 2", "Solve for x:  2x + 6 = 10.") is False
    assert verify("5", "x = 2", "Solve for x:  2x + 6 = 10.") is False


def test_verify_is_false_without_a_reference_answer():
    # No reference answer means nothing to compare against -- the tool has
    # no opinion, and `verify()`'s bool contract renders that as False
    # (contracts.py's ACT step data), not a guess reconstructed from the
    # problem statement.
    assert verify("2", "", "Solve for x:  2x + 6 = 10.") is False
