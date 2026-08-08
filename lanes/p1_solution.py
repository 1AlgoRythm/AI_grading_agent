"""P1 solution and rubric drafting helpers.

Contains `develop_solution` and `draft_rubric` which call the shared
`model_provider`. These functions keep conservative fallbacks for offline
operation so the walking skeleton and tests remain deterministic.
"""
from __future__ import annotations

import re

import fixtures
from contracts import ArtifactStatus, Problem, Assignment, Rubric, SolutionSource
from model_provider import call_model, call_model_json

__all__ = ["develop_solution", "draft_rubric"]


def develop_solution(problem: Problem) -> Problem:
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

    method_hint = getattr(problem, "method_hint", None)
    prompt = (
        f"Draft a concise model solution and a one-line final answer for the problem:\n{problem.statement}\n"
    )
    if method_hint:
        prompt += f"Method hint:\n{method_hint}\n"
    raw = call_model(prompt, max_tokens=512)
    ans = None
    sol = raw.strip()
    for line in raw.splitlines():
        if line.lower().startswith("final answer:"):
            ans = line.split("final answer:", 1)[1].strip()
            break
    problem.reference_solution = sol
    problem.reference_answer = ans
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
            generated_criteria.append({
                "problem_id": p.id,
                "name": "Method / shown work",
                "description": "Key steps of a valid method are shown; small arithmetic slips tolerated.",
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
