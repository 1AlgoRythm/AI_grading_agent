"""P1 solution and rubric drafting helpers.

Contains `develop_solution`, `draft_rubric`, and `verify_solution`, which call
the shared `model_provider`. These functions keep conservative fallbacks for
offline operation so the walking skeleton and tests remain deterministic.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import fixtures
from contracts import ArtifactStatus, Problem, Assignment, Rubric, SolutionSource
from model_provider import call_model, call_model_json
from lanes.p2_verify import check_equivalence

__all__ = ["develop_solution", "draft_rubric", "verify_solution"]


def _generate_solution_text(problem: Problem, method_context: Optional[str]) -> tuple[str, Optional[str]]:
    """Call the model for an independent solution, grounded in the retrieved
    course method when one is available. Returns (solution_text, final_answer)."""
    prompt = (
        f"Draft a concise model solution and a one-line final answer for the problem:\n{problem.statement}\n"
    )
    if method_context:
        prompt += f"Ground your method in this course material:\n{method_context}\n"
    raw = call_model(prompt, max_tokens=512)
    ans = None
    for line in raw.splitlines():
        if line.lower().startswith("final answer:"):
            # Split on the generic ":" (not the case-sensitive literal
            # "final answer:") so this doesn't break on the very likely case
            # of a real model capitalizing it as "Final answer:".
            ans = line.split(":", 1)[1].strip()
            break
    return raw.strip(), ans


def _develop_with_sample_cross_check(
    problem: Problem,
    method_context: Optional[str],
    sample_solution: tuple[str, Optional[str]],
) -> Problem:
    """A sample was provided (e.g. by the instructor): generate an
    independent solution and cross-check it against the sample. Agreement ->
    trust the human-provided sample. Disagreement -> flag it directly inside
    the reference_solution text so a human reviewing that exact artifact sees
    both versions before approving anything (plan §10/§15: "cross-check
    generated vs. sample; disagreements flag for human review")."""
    sample_text, sample_answer = sample_solution
    generated_text, generated_answer = _generate_solution_text(problem, method_context)

    agrees = bool(generated_answer and sample_answer and check_equivalence(generated_answer, sample_answer))
    if agrees or not generated_answer:
        problem.reference_solution = sample_text
        problem.reference_answer = sample_answer
        problem.solution_source = SolutionSource.SAMPLE
    else:
        problem.reference_solution = (
            "DISAGREEMENT FLAGGED FOR HUMAN REVIEW: the independently generated answer "
            f"({generated_answer!r}) does not match the provided sample's answer "
            f"({sample_answer!r}). Compare both before approving.\n\n"
            f"--- Provided sample ---\n{sample_text}\n\n"
            f"--- Independently generated ---\n{generated_text}"
        )
        problem.reference_answer = sample_answer
        problem.solution_source = SolutionSource.GENERATED
    problem.solution_status = ArtifactStatus.PROPOSED
    return problem


def develop_solution(
    problem: Problem,
    method_context: Optional[str] = None,
    sample_solution: Optional[tuple[str, Optional[str]]] = None,
) -> Problem:
    """Populate problem.reference_solution/reference_answer (still PROPOSED —
    a human approves separately, never this function). Three paths, tried in
    order:
      1. `sample_solution` given -> cross-check it against an independently
         generated solution; disagreement is flagged for human review.
      2. No sample, but the problem matches a bundled demo fixture (walking-
         skeleton convenience) -> use its known-good reference.
      3. Otherwise, generate from scratch, grounded in `method_context` (the
         retrieved course method) when one was found.
    """
    if sample_solution is not None:
        return _develop_with_sample_cross_check(problem, method_context, sample_solution)

    try:
        ref = {p.label: p for p in fixtures.sample_assignment().problems}[problem.label]
    except Exception:
        ref = None
    if ref:
        problem.reference_solution = ref.reference_solution
        problem.reference_answer = ref.reference_answer
        problem.solution_source = SolutionSource.GENERATED
        problem.solution_status = ArtifactStatus.PROPOSED
        return problem

    problem.reference_solution, problem.reference_answer = _generate_solution_text(problem, method_context)
    problem.solution_source = SolutionSource.GENERATED
    problem.solution_status = ArtifactStatus.PROPOSED
    return problem


def draft_rubric(assignment: Assignment, method_context: dict) -> Rubric:
    r = fixtures.sample_rubric()
    method_snippets = []
    for pid, snippet in method_context.items():
        if snippet:
            method_snippets.append(f"Problem {pid.hex[-2:]} method: {snippet}")

    prompt = f"Draft a lenient per-problem rubric. Assignment: {assignment.label}\n"
    if method_snippets:
        prompt += "\n".join(method_snippets) + "\n"
    raw = call_model_json(prompt, max_tokens=1024)
    criteria = None
    if isinstance(raw, dict) and "criteria" in raw:
        criteria = raw["criteria"]
        if isinstance(criteria, str):
            try:
                import json

                parsed = json.loads(criteria)
                if isinstance(parsed, list):
                    criteria = parsed
                else:
                    criteria = None
            except Exception:
                criteria = None

    if not criteria:
        generated_criteria = []
        for p in assignment.problems:
            generated_criteria.append({
                "problem_id": p.id,
                "name": "Correct final answer",
                "description": "Final answer matches the approved reference.",
                "points": p.points_possible,
            })
            method_description = "Key steps of a valid method are shown; small arithmetic slips tolerated."
            method_snippet = method_context.get(p.id)
            if method_snippet:
                # Bake the retrieved course method into the rubric itself (retrieval
                # happens only here, at rubric-design time — never at grading time).
                method_description += f" Method from course material: {method_snippet}"
            generated_criteria.append({
                "problem_id": p.id,
                "name": "Method / shown work",
                "description": method_description,
                "points": max(0.5, p.points_possible * 0.5),
            })
        try:
            r.criteria = generated_criteria
        except Exception:
            pass
    else:
        try:
            r.criteria = criteria
        except Exception:
            r.criteria = criteria
    r.status = ArtifactStatus.PROPOSED
    return r


def _model_configured() -> bool:
    return bool(os.getenv("MODEL_PROVIDER") and os.getenv("MODEL_API_KEY"))


def _self_consistency_check(problem: Problem) -> Optional[tuple[bool, str]]:
    """Re-derive an answer independently and compare via P2's tool.

    Only runs a real re-derivation when a real BYOK provider is configured —
    the generic offline stub isn't grading-aware, so calling it here would
    just fabricate a false "disagreement" on every solution. Returns None
    when the check cannot meaningfully run.
    """
    if not _model_configured():
        return None
    prompt = (
        "Solve this problem independently, showing brief work, and end your "
        "response with a line of the exact form 'Final answer: <value>'.\n"
        f"{problem.statement}"
    )
    raw = call_model(prompt, max_tokens=256)
    rederived: Optional[str] = None
    for line in raw.splitlines():
        if line.lower().startswith("final answer:"):
            rederived = line.split(":", 1)[1].strip()
            break
    if not rederived:
        return None
    agrees = check_equivalence(problem.reference_answer or "", rederived)
    note = (
        f"Self-consistency re-derivation {'agrees' if agrees else 'disagrees'} with "
        f"the proposed answer (got {rederived!r})."
    )
    return agrees, note


def _prep_expr(expr: str) -> str:
    expr = expr.replace("^", "**")
    return re.sub(r"(\d)([a-zA-Z])", r"\1*\2", expr)


def _substitution_check(statement: str, answer: str) -> Optional[tuple[bool, str]]:
    """Best-effort: for a 'solve for <var>' problem, substitute the proposed
    answer back into the original equation and check it actually satisfies
    it. Returns None (skip, not a failure) when the statement/answer aren't
    in a simple single-equation, single-variable form this can parse."""
    var_match = re.search(r"\b([a-zA-Z])\s*=\s*(.+)", answer.strip())
    if not var_match:
        return None

    region = statement.split(":")[-1]
    eq_match = re.search(r"([^=]+)=([^=]+)", region)
    if not eq_match:
        return None

    var, value_str = var_match.group(1), var_match.group(2).strip()
    lhs_str = eq_match.group(1).strip().rstrip(".,;")
    rhs_str = eq_match.group(2).strip().rstrip(".,;")
    try:
        import sympy as sp

        symbol = sp.symbols(var)
        value = sp.sympify(_prep_expr(value_str))
        lhs = sp.sympify(_prep_expr(lhs_str))
        rhs = sp.sympify(_prep_expr(rhs_str))
        satisfied = sp.simplify(lhs.subs(symbol, value) - rhs.subs(symbol, value)) == 0
    except Exception:
        return None

    verb = "satisfies" if satisfied else "does NOT satisfy"
    return satisfied, f"Substitution check: {var} = {value} {verb} the original equation."


def verify_solution(problem: Problem) -> tuple[bool, str]:
    """Best-effort validation of a proposed solution (decision: never
    approves — a human approves). Runs self-consistency (re-derive and
    compare) and, where the problem is a simple solvable equation,
    substitution (plug the answer back in and check it holds)."""
    if not problem.reference_answer or not problem.reference_solution:
        return False, "No proposed solution to verify yet."

    notes: list[str] = []
    ok = True
    ran_any_check = False

    consistency = _self_consistency_check(problem)
    if consistency is not None:
        ran_any_check = True
        agrees, note = consistency
        ok = ok and agrees
        notes.append(note)
    else:
        notes.append("Self-consistency check skipped (no BYOK model provider configured).")

    substitution = _substitution_check(problem.statement, problem.reference_answer)
    if substitution is not None:
        ran_any_check = True
        satisfied, note = substitution
        ok = ok and satisfied
        notes.append(note)
    else:
        notes.append("Substitution check skipped (not a simple solvable equation).")

    if not ran_any_check:
        return False, " ".join(notes) + " No verification could be performed; needs human review."
    return ok, " ".join(notes)
