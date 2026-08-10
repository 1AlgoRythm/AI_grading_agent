"""P1 solution and rubric drafting helpers.

Contains `develop_solution`, `draft_rubric`, and `verify_solution`, which call
the shared `model_provider`. These functions keep conservative fallbacks for
offline operation so the walking skeleton and tests remain deterministic.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

import fixtures
from contracts import ArtifactStatus, Problem, Assignment, Rubric, SolutionSource, known_assignment_types, new_id
from model_provider import call_model_json
from lanes.p2_verify import check_equivalence

__all__ = ["classify_problem_type", "develop_solution", "draft_rubric", "verify_solution", "ProblemTypeClassification"]


@dataclass(frozen=True)
class ProblemTypeClassification:
    type: str
    confident: bool


def classify_problem_type(problem_statement: str) -> ProblemTypeClassification:
    """Ask the model what assignment type a single problem is (math,
    short_answer, proof, ...) so the right verifier (lanes/p2_tools.py)
    routes automatically per problem -- one dropdown can't pick a single
    type for a mixed assignment, and per-problem classification is what
    actually reflects that.

    On low confidence or an unparseable response, falls back to a
    not-confident "short_answer" classification -- routing to the
    judgment-only verifier, never a false objective check. A wrong
    "no objective check" is still a subjective grade that gets a critic and
    a human gate; a wrong objective check is a confidently incorrect verdict
    handed to the grader as settled fact.
    """
    known = ", ".join(sorted(known_assignment_types()))
    prompt = (
        f"Classify this problem's assignment type. Known types: {known}. "
        "If none of those fit, propose a short lowercase type name (e.g. \"code\").\n\n"
        f"Problem: {problem_statement}\n\n"
        "Respond with ONLY a JSON object: {\"type\": <string>, \"confident\": <true|false>}."
    )
    raw = call_model_json(prompt, max_tokens=64)
    if isinstance(raw, dict) and raw.get("type"):
        type_name = str(raw["type"]).strip().lower()
        if type_name:
            return ProblemTypeClassification(type=type_name, confident=bool(raw.get("confident", True)))
    return ProblemTypeClassification(type="short_answer", confident=False)


def _generate_solution_text(problem: Problem, method_context: Optional[str]) -> tuple[str, Optional[str]]:
    """Call the model for an independent solution, grounded in the retrieved
    course method when one is available. Returns (solution_text, final_answer).

    Uses `call_model_json` (structured output), not free-text line-scanning
    for a literal "Final answer:" marker -- real models routinely phrase
    their final line differently even when asked to use that exact marker,
    which silently lost the answer. JSON output is the same reliable pattern
    already used for the grader/critic prompts."""
    prompt = (
        "Draft a concise model solution for this problem, showing the key steps.\n"
        f"Problem: {problem.statement}\n"
    )
    if method_context:
        prompt += f"Ground your method in this course material:\n{method_context}\n"
    prompt += (
        "\nRespond with ONLY a JSON object: {\"solution\": <string with the worked "
        "solution>, \"final_answer\": <string with just the final answer>}."
    )
    raw = call_model_json(prompt, max_tokens=512)
    if isinstance(raw, dict) and raw.get("solution"):
        answer = raw.get("final_answer")
        return str(raw["solution"]).strip(), str(answer).strip() if answer else None
    # Offline stub / unparseable response: no structured solution came back.
    return "", None


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
    # Only take the demo-fixture shortcut when no real model is configured.
    # With a real key, a problem that happens to be labeled Q1/Q2 (the
    # default auto-label for the 1st/2nd numbered problem in ANY assignment)
    # would otherwise silently get the fixture's canned answer instead of a
    # real one -- looking "brilliantly solved" while never touching the key.
    if ref and not _model_configured():
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
    # sample_rubric() carries the *fixture's* own id and assignment_id --
    # both must be repointed at a fresh id / the real assignment. Leaving
    # `id` as the fixture's meant every drafted rubric for every assignment
    # shared the same primary key: P1Store.save_rubric merges on `id` and
    # deletes criteria by rubric_id, so drafting a second assignment's
    # rubric silently overwrote the first one's row and wiped its criteria.
    r.id = new_id()
    r.assignment_id = assignment.id
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
                # Capped: build_context sums this into estimated_tokens against
                # DEFAULT_TOKEN_BUDGET, and multiple criteria/problems each carry
                # their own snippet -- an uncapped textbook excerpt (a CLRS
                # section is much larger than algebra.txt) could blow the budget.
                method_description += f" Method from course material: {method_snippet[:800]}"
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
        "Solve this problem independently, showing brief work.\n"
        f"Problem: {problem.statement}\n\n"
        "Respond with ONLY a JSON object: {\"final_answer\": <string with just the "
        "final answer>}."
    )
    raw = call_model_json(prompt, max_tokens=256)
    rederived = raw.get("final_answer") if isinstance(raw, dict) else None
    rederived = str(rederived).strip() if rederived else None
    if not rederived:
        return None
    if not _looks_symbolic(problem.reference_answer or "") or not _looks_symbolic(rederived):
        # check_equivalence is SymPy-backed: it can't parse free-form prose
        # (a proof, a written explanation), and would silently return False
        # for two answers that are both correct but worded differently --
        # reporting that as "disagrees" would be a false, misleading signal,
        # worse than just admitting this check doesn't apply here.
        return None
    agrees = check_equivalence(problem.reference_answer or "", rederived)
    note = (
        f"Self-consistency re-derivation {'agrees' if agrees else 'disagrees'} with "
        f"the proposed answer (got {rederived!r})."
    )
    return agrees, note


def _looks_symbolic(text: str) -> bool:
    """Best-effort check for a short symbolic expression (equivalence
    checking can meaningfully compare these) vs. free-form prose like a
    proof or written explanation (it can't)."""
    stripped = text.strip()
    if not stripped or len(stripped.split()) > 12:
        return False
    return bool(re.search(r"[0-9=+\-*/^]", stripped))


def verify_solution(problem: Problem) -> tuple[bool, str]:
    """Best-effort validation of a proposed solution (decision: never
    approves -- a human approves) via self-consistency: re-derive the answer
    independently through the LLM and compare it with P2's check_equivalence
    tool.

    A regex-based substitution check used to run here too (extract the one
    equation from the raw problem statement, plug the answer back in).
    Removed because it only ever worked for the narrowest textbook phrasing
    ("Solve for x: ...") and gave false confidence everywhere else --
    assignments aren't fixed to that shape, so the check that actually
    generalizes is the LLM's own re-derivation plus the equivalence tool,
    not a hand-rolled parser standing in for one.
    """
    if not problem.reference_answer or not problem.reference_solution:
        return False, "No proposed solution to verify yet."

    consistency = _self_consistency_check(problem)
    if consistency is not None:
        return consistency

    if not _model_configured():
        note = "Self-consistency check skipped (no BYOK model provider configured)."
    elif not _looks_symbolic(problem.reference_answer or ""):
        note = (
            "Self-consistency check skipped: the reference answer is free-form prose "
            "(e.g. a proof or written explanation), not a short symbolic expression "
            "equivalence checking can meaningfully compare -- rely on human review here."
        )
    else:
        note = "Self-consistency check skipped: no comparable answer was re-derived."
    return False, f"{note} No verification could be performed; needs human review."
