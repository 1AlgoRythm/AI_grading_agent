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
from lanes.p2_tools import get_verifier
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
    raw = call_model_json(prompt, max_tokens=128)
    if isinstance(raw, dict) and raw.get("type"):
        type_name = str(raw["type"]).strip().lower()
        if type_name:
            if not bool(raw.get("confident", True)):
                # A parseable-but-unconfident response used to keep the
                # model's proposed type verbatim (e.g. type="math",
                # confident=False), which still routed to the SymPy
                # objective verifier as if confidently typed -- exactly the
                # "confidently incorrect verdict" this docstring promises
                # never happens on low confidence.
                return ProblemTypeClassification(type="short_answer", confident=False)
            return ProblemTypeClassification(type=type_name, confident=True)
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
        # Grounding, not copy-paste: the retrieved chunk is a coarse
        # relevance-gated match (lanes/p1_rag.py), not a guaranteed fit for
        # THIS problem -- an irrelevant chunk must be ignorable, and even a
        # relevant one must never be quoted verbatim into student-facing
        # output.
        prompt += (
            f"Course material retrieved as reference grounding:\n{method_context}\n"
            "Use it only if it is actually relevant to solving THIS problem; if it "
            "is not relevant, ignore it entirely and solve from first principles. "
            "Never quote or copy this material verbatim -- write the solution in "
            "your own words.\n"
        )
    prompt += (
        "\nRespond with ONLY a JSON object: {\"solution\": <string with the worked "
        "solution>, \"final_answer\": <string with ONLY the final answer as a short "
        "expression -- no working, no steps, repeated here from the solution field>}."
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


def _generated_criteria(assignment: Assignment, method_context: dict) -> list[dict]:
    """Offline template: four proportioned criteria per problem instead of
    two. The old template gave "final answer" the full points AND "method"
    half the points on top -- 1.5x the problem's total, which only worked
    because nothing mechanically sums criterion points (grading is a single
    holistic judgment against the whole problem's points_possible). Still
    true here, but a real BYOK grader reads these criteria as descriptive
    context for how to weight partial credit, so a rubric with only one or
    two generic criteria gives it much less to reason with than an
    instructor's actual multi-facet rubric would."""
    generated_criteria = []
    for p in assignment.problems:
        possible = p.points_possible

        generated_criteria.append({
            "problem_id": p.id,
            "name": "Correct final answer",
            "description": "Final answer matches the approved reference.",
            "points": round(possible * 0.5, 4),
            "failure_signals": ["final answer differs from the reference"],
        })

        # Grounding, not copy-paste: the offline template has no model to ask
        # to paraphrase, so it never embeds the retrieved snippet's actual
        # text at all -- only a generic note that grounding was available.
        # The old version spliced the raw snippet (capped at 800 chars)
        # straight into this description, which is exactly the verbatim
        # copy this criterion (and everything downstream that reads it --
        # build_context, feedback) must never carry.
        method_description = "A valid method or approach is used to reach the answer."
        if method_context.get(p.id):
            method_description += " Informed by the retrieved course material where relevant."
        generated_criteria.append({
            "problem_id": p.id,
            "name": "Valid method or approach",
            "description": method_description,
            "points": round(possible * 0.25, 4),
            "failure_signals": ["no method shown", "answer stated without working"],
        })

        generated_criteria.append({
            "problem_id": p.id,
            "name": "Clearly shown work",
            "description": (
                "Key intermediate steps are shown clearly enough to follow the reasoning, "
                "even if the final answer has a minor arithmetic slip."
            ),
            "points": round(possible * 0.15, 4),
        })

        generated_criteria.append({
            "problem_id": p.id,
            "name": "Free of unjustified leaps or errors",
            "description": (
                "No unexplained jumps in logic and no errors beyond the minor arithmetic "
                "slips the leniency policy already tolerates."
            ),
            "points": round(possible * 0.10, 4),
        })
    return generated_criteria


def _normalize_and_fill(
    criteria: list[dict], assignment: Assignment, method_context: dict
) -> tuple[list[dict], list[str]]:
    """Enforce the two invariants draft_rubric's prompt asks for but nothing
    ever checked before this: every problem must have at least one
    criterion, and each problem's criteria must sum to that problem's
    points_possible. A rubric violating either reaches the grader as a
    silently wrong weighting -- or, for an uncovered problem, as
    points_possible = 0 in p2_engine's no-context branch. Rescaling
    preserves the model's judgment about *relative* weights while enforcing
    the total; only a problem with no criteria at all falls back to the
    offline template.

    Returns (criteria, notes) -- notes are surfaced to the human reviewer
    rather than silently swallowed, since a repaired rubric is exactly the
    thing the approval gate exists to catch.
    """
    template_by_problem: dict = {}
    notes: list[str] = []
    out: list[dict] = []

    for problem in assignment.problems:
        mine = [c for c in criteria if c.get("problem_id") == problem.id]

        if not mine:
            if not template_by_problem:
                for entry in _generated_criteria(assignment, method_context):
                    template_by_problem.setdefault(entry["problem_id"], []).append(entry)
            out.extend(template_by_problem.get(problem.id, []))
            notes.append(
                f"{problem.label}: the model returned no criteria; filled in from the "
                f"default template. Review this problem's rubric closely."
            )
            continue

        total = sum(float(c.get("points") or 0) for c in mine)
        if total <= 0:
            share = round(problem.points_possible / len(mine), 4)
            for c in mine:
                c["points"] = share
            notes.append(f"{problem.label}: criteria had no positive points; split evenly.")
        elif abs(total - problem.points_possible) > 0.01:
            factor = problem.points_possible / total
            for c in mine:
                c["points"] = round(float(c.get("points") or 0) * factor, 4)
            notes.append(
                f"{problem.label}: criteria summed to {total:g}, rescaled to "
                f"{problem.points_possible:g}."
            )
        out.extend(mine)

    return out, notes


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

    # The model has no way to know the real problem UUIDs unless we hand
    # them over -- give it each one to echo back verbatim. Every criterion
    # it returns still gets validated against this map below (never trusted
    # blind): a model that invents/paraphrases a problem_id instead of
    # copying it used to reach `Rubric.criteria = criteria` unchanged, and
    # Pydantic's required `problem_id: UUID` field raised uncaught the
    # instant any criterion lacked a real one -- which is every criterion,
    # since nothing ever told the model what a real id even looks like.
    problems_by_id = {str(p.id): p.id for p in assignment.problems}
    problem_lines = [f'- problem_id "{p.id}" ({p.label}): {p.statement}' for p in assignment.problems]
    method_snippets = []
    for pid, snippet in method_context.items():
        if snippet:
            method_snippets.append(f"Problem {pid.hex[-2:]} method: {snippet}")

    prompt = (
        f"Draft a lenient per-problem rubric. Assignment: {assignment.label}\n"
        + "\n".join(problem_lines) + "\n"
    )
    if method_snippets:
        prompt += "\n".join(method_snippets) + "\n"
        # Grounding, not copy-paste: the retrieved chunk is a coarse
        # relevance-gated match (lanes/p1_rag.py), not guaranteed to fit
        # every problem it's handed alongside -- an irrelevant one must be
        # ignorable, and even a relevant one must never be quoted verbatim
        # into a criterion a student will eventually read.
        prompt += (
            "The course material above is reference grounding only: use it for a "
            "problem only if it is actually relevant to THAT problem; if it is not "
            "relevant, ignore it entirely. Never quote or copy this material "
            "verbatim into a criterion's name or description -- paraphrase in your "
            "own words if you use it at all.\n"
        )
    prompt += (
        "For each problem, break the rubric into multiple distinct criteria that "
        "separately assess the different things a grader would actually check -- e.g. "
        "the final answer, the method/approach, how clearly the work is shown, whether "
        "the reasoning has unjustified leaps -- but a problem-specific breakdown you judge "
        "more useful is better than forcing every problem into the same mold. Decide the "
        "number of criteria yourself, based on how many distinct things are actually worth "
        "grading separately for THAT problem -- a one-line short-answer question may "
        "genuinely need only one or two; a multi-step derivation may need four or five. "
        "Each problem's criteria points should sum to that problem's total points_possible "
        "given above.\n"
        "For each criterion also give 2-4 \"failure_signals\": short, concrete, "
        "literal phrases a grader would find in flawed student work for THIS "
        "problem (e.g. \"middle term missing\", \"sign error when collecting like "
        "terms\"). These are matched against the shown work, so write them as "
        "things a student's writing would actually contain, not as abstract "
        "descriptions of the mistake.\n"
        "For each criterion, set \"problem_id\" to EXACTLY one of the problem_id "
        "strings given above (copy it verbatim) -- never invent a new one.\n"
        "Respond with ONLY a JSON object: {\"criteria\": [{\"problem_id\": <string>, "
        "\"name\": <string>, \"description\": <string>, \"points\": <number>, "
        "\"failure_signals\": [<string>, ...]}, ...]}."
    )
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

    valid_criteria = []
    if isinstance(criteria, list):
        for item in criteria:
            if not isinstance(item, dict):
                continue
            real_id = problems_by_id.get(str(item.get("problem_id")))
            if real_id is None:
                continue
            signals = item.get("failure_signals")
            if isinstance(signals, str):
                signals = [signals]
            signals = [str(s).strip() for s in signals if str(s).strip()] if isinstance(signals, list) else []
            valid_criteria.append({**item, "problem_id": real_id, "failure_signals": signals})

    criteria = valid_criteria or _generated_criteria(assignment, method_context)
    r.criteria, notes = _normalize_and_fill(criteria, assignment, method_context)
    if notes:
        r.leniency_note = (r.leniency_note or "") + (
            "\n\nAutomatic rubric checks repaired the following before review: "
            + "; ".join(notes)
        )
    r.status = ArtifactStatus.PROPOSED
    return r


def _model_configured() -> bool:
    return bool(os.getenv("MODEL_PROVIDER") and os.getenv("MODEL_API_KEY"))


def _extract_short_answer(text: str) -> Optional[str]:
    """Best-effort salvage when the model puts its working in the answer
    field despite being told not to (models don't always obey). Tries, in
    order: a trailing "<name> = <value>" assignment (e.g. "...divide by 3,
    y = 7" -> "y = 7" -- keeps the variable name, since the reference answer
    likely has it too and a bare "7" wouldn't match "y = 7"), then the tail
    after the last "=", then the last non-blank line. Returns None (don't
    guess) if no candidate is actually short and symbolic -- a failed
    salvage should fall through to "skipped," never to a comparison against
    a stray fragment of a sentence.
    """
    candidates = []
    trailing_assignment = re.search(r"([a-zA-Z]\w*\s*=\s*[^,;.\n]+?)\s*[.,]?\s*$", text)
    if trailing_assignment:
        candidates.append(trailing_assignment.group(1).strip())
    if "=" in text:
        candidates.append(text.rsplit("=", 1)[-1].strip())
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        candidates.append(lines[-1])
    for candidate in candidates:
        if _looks_symbolic(candidate):
            return candidate
    return None


def _self_consistency_check(problem: Problem, problem_type: Optional[str] = None) -> Optional[tuple[bool, str]]:
    """Re-derive an answer independently and compare via P2's tool.

    Only runs a real re-derivation when a real BYOK provider is configured —
    the generic offline stub isn't grading-aware, so calling it here would
    just fabricate a false "disagreement" on every solution. Returns None
    when the check cannot meaningfully run.

    When `problem_type` is known (from fix #7's classifier), it is the
    authoritative signal for whether this problem even has a symbolically
    comparable answer -- checked via the same verifier registry grading
    uses, so "does this type get an objective check" is answered in exactly
    one place, not decided twice by two different heuristics that could
    disagree. Without it (existing callers that predate per-problem
    classification), falls back to `_looks_symbolic` on the reference answer
    text alone. That fallback is a real, live gap: a proof's *final answer*
    field is a short conclusion sentence (e.g. "x^2 is even."), which can
    slip under `_looks_symbolic`'s word-count guard and get compared as if
    it were a symbolic expression, producing a nonsense "disagrees" verdict
    on a correct proof. `problem_type` closes that gap when it's available.
    """
    if not _model_configured():
        return None
    if problem_type is not None:
        if get_verifier(problem_type).name != "sympy_math":
            return None
    elif not _looks_symbolic(problem.reference_answer or ""):
        # No known type -- fall back to guessing from the text. The
        # reference looks like prose (a proof, a written explanation), so
        # equivalence checking has no opinion here no matter what comes
        # back, and there's no point spending a call finding that out.
        return None

    prompt = (
        "Solve this problem independently.\n"
        f"Problem: {problem.statement}\n\n"
        "Respond with ONLY a JSON object: {\"final_answer\": <string>}. The "
        "final_answer value must be ONLY the final answer as a short "
        "expression -- no working, no steps, no explanation. For example "
        "\"y = 7\" or \"x^2 + x - 6\", never a sentence describing how you "
        "got there."
    )
    raw = call_model_json(prompt, max_tokens=512)
    rederived = raw.get("final_answer") if isinstance(raw, dict) else None
    rederived = str(rederived).strip() if rederived else None
    if not rederived:
        return None

    if not _looks_symbolic(rederived):
        # Asked for the answer only and got working anyway -- try to salvage
        # a short expression before giving up on the check entirely.
        rederived = _extract_short_answer(rederived)
        if rederived is None:
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


def verify_solution(problem: Problem, problem_type: Optional[str] = None) -> tuple[Optional[bool], str]:
    """Best-effort validation of a proposed solution (decision: never
    approves -- a human approves) via self-consistency: re-derive the answer
    independently through the LLM and compare it with P2's check_equivalence
    tool.

    `problem_type` (from fix #7's classifier) is optional and additive --
    when given, it authoritatively decides whether this problem's answer is
    symbolically comparable at all (via the same verifier registry grading
    uses), which is more reliable than guessing from the reference answer's
    text alone. Every existing caller that doesn't know about per-problem
    types yet keeps the exact same text-heuristic behavior it always had.

    Returns (agrees, note), where `agrees` is three-state, not a plain bool:
    `True`/`False` when the check actually ran and reached a verdict, `None`
    when it could not run at all (no model configured, a prose reference, no
    comparable answer re-derived, or no solution yet). Collapsing "could not
    run" into `False` would make a skipped check indistinguishable from a
    genuine disagreement to any caller reading the value as a bool --
    exactly the ambiguity `lanes/p2_tools.py`'s three-state verdict exists to
    avoid on the grading side; this is the same principle applied here.

    A regex-based substitution check used to run here too (extract the one
    equation from the raw problem statement, plug the answer back in).
    Removed because it only ever worked for the narrowest textbook phrasing
    ("Solve for x: ...") and gave false confidence everywhere else --
    assignments aren't fixed to that shape, so the check that actually
    generalizes is the LLM's own re-derivation plus the equivalence tool,
    not a hand-rolled parser standing in for one.
    """
    if not problem.reference_answer or not problem.reference_solution:
        return None, "No proposed solution to verify yet."

    consistency = _self_consistency_check(problem, problem_type)
    if consistency is not None:
        return consistency

    if not _model_configured():
        note = "Self-consistency check skipped (no BYOK model provider configured)."
    elif problem_type is not None and get_verifier(problem_type).name != "sympy_math":
        note = (
            f"Self-consistency check skipped: problem type {problem_type!r} has no "
            "objective check -- rely on human review here."
        )
    elif problem_type is None and not _looks_symbolic(problem.reference_answer or ""):
        note = (
            "Self-consistency check skipped: the reference answer is free-form prose "
            "(e.g. a proof or written explanation), not a short symbolic expression "
            "equivalence checking can meaningfully compare -- rely on human review here."
        )
    else:
        note = "Self-consistency check skipped: no comparable answer was re-derived."
    return None, f"{note} No verification could be performed; needs human review."
