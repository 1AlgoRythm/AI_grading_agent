"""[P1] Context engineering helpers.

P1 owns the curated grading context. This module assembles the per-problem
`GradingContext` objects that P2 consumes, and it trims student work to stay
within the token budget established by the shared contracts.

Exports:
- `build_context(problem, submission, rubric)`
- `build_submission_context(assignment, submission, rubric)`
"""
from __future__ import annotations

from typing import List

from contracts import (
    ArtifactStatus,
    Assignment,
    DEFAULT_TOKEN_BUDGET,
    GradingContext,
    Problem,
    Submission,
    SubmissionContext,
    rough_token_estimate,
)
from fixtures import GRADING_POLICY

__all__ = ["build_context", "build_submission_context"]


def build_context(
    problem: Problem,
    submission: Submission,
    rubric,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> GradingContext:
    # Human-in-the-loop gate (a hard rule per the project plan, not just a
    # convention): grading context may never be assembled from a solution or
    # rubric a human hasn't approved yet, since a wrong reference would
    # silently corrupt every grade built on top of it.
    if problem.solution_status is not ArtifactStatus.APPROVED:
        raise ValueError(
            f"cannot build grading context for problem '{problem.label}': its solution "
            f"is {problem.solution_status.value}, not approved. A human must approve the "
            f"solution before it can be used to grade."
        )
    if rubric.status is not ArtifactStatus.APPROVED:
        raise ValueError(
            f"cannot build grading context: rubric v{rubric.version} is {rubric.status.value}, "
            f"not approved. A human must approve the rubric before it can be used to grade."
        )

    answer = submission.answer_for(problem.id)
    # All rubric criteria for this problem are always relevant — they are
    # injected in full, never capped. Only the student's own shown work
    # varies in size per submission, so that is the one part trimmed if the
    # assembled context runs over budget (§6: inject what you already know
    # is relevant; the rubric and grading policy are never dropped).
    criteria = rubric.for_problem(problem.id)
    work = answer.work_text if answer else ""
    final = answer.final_answer if answer else None
    parts = [problem.statement or "", problem.reference_solution or "", GRADING_POLICY, work]
    for c in criteria or []:
        desc = c.get("description") if isinstance(c, dict) else getattr(c, "description", "")
        parts.append(desc or "")
    est = sum(rough_token_estimate(p) for p in parts)
    trimmed_work = work
    while est > token_budget and trimmed_work:
        trimmed_work = trimmed_work[: int(len(trimmed_work) * 0.7)]
        parts[3] = trimmed_work
        est = sum(rough_token_estimate(p) for p in parts)
    return GradingContext(
        problem_id=problem.id,
        problem_statement=problem.statement,
        reference_solution=problem.reference_solution or "",
        reference_answer=problem.reference_answer,
        rubric_criteria=criteria,
        grading_policy=GRADING_POLICY,
        student_work=trimmed_work,
        student_final_answer=final,
        points_possible=problem.points_possible,
        token_budget=token_budget,
        estimated_tokens=est,
    )


def build_submission_context(
    assignment: Assignment,
    submission: Submission,
    rubric,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> SubmissionContext:
    problems = {p.id: p for p in assignment.problems}
    contexts: List[GradingContext] = [
        build_context(problems[a.problem_id], submission, rubric, token_budget=token_budget)
        for a in submission.answers
    ]
    return SubmissionContext(submission_id=submission.id, problem_contexts=contexts)
