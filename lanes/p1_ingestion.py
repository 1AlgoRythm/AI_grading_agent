"""[P1] Ingestion, context & rubric.

SKELETON stubs: every body here is a trivial placeholder so the end-to-end path
runs. Replace each body with real logic (file parsing, textbook retrieval, LLM
solution/rubric drafting, real context assembly) — but keep the signatures,
because they are the frozen seam other lanes build against.

The fixture imports below are ONLY a stand-in data source for the skeleton. Real
P1 code parses uploaded files instead and should not import fixtures.
"""

from __future__ import annotations

import fixtures
from contracts import (
    ArtifactStatus,
    Assignment,
    GradingContext,
    Problem,
    Rubric,
    SolutionSource,
    Submission,
    SubmissionContext,
    rough_token_estimate,
)
from fixtures import GRADING_POLICY


def ingest_assignment(source: str) -> Assignment:
    """SKELETON: pretend to parse `source` into problems, WITHOUT solutions yet
    (solutions are developed and approved in later stages). Real P1 parses a PDF
    / notebook here."""
    a = fixtures.sample_assignment()
    for p in a.problems:                      # strip solutions: not developed yet
        p.reference_solution = None
        p.reference_answer = None
        p.solution_source = None
        p.solution_status = ArtifactStatus.PROPOSED
    return a


def develop_solution(problem: Problem) -> Problem:
    """SKELETON: copy the known reference from the fixture by label. Real P1
    generates the solution with an LLM (or ingests + cross-checks a sample).
    Leaves status PROPOSED — a human approves separately."""
    ref = {p.label: p for p in fixtures.sample_assignment().problems}[problem.label]
    problem.reference_solution = ref.reference_solution
    problem.reference_answer = ref.reference_answer
    problem.solution_source = SolutionSource.GENERATED
    problem.solution_status = ArtifactStatus.PROPOSED
    return problem


def draft_rubric(assignment: Assignment, method_context: dict) -> Rubric:
    """SKELETON: return the fixture rubric as a PROPOSED draft. Real P1 drafts it
    with an LLM from the spec + retrieved method (`method_context`)."""
    r = fixtures.sample_rubric()
    r.status = ArtifactStatus.PROPOSED
    return r


def ingest_submission(source: str) -> Submission:
    """SKELETON: return the fixture submission. Real P1 parses a student file and
    sanitizes it against prompt injection."""
    return fixtures.sample_submission()


def build_context(problem: Problem, submission: Submission, rubric: Rubric) -> GradingContext:
    """Assemble the curated, budget-checked context for ONE problem. This body is
    already close to real: it selects the pieces and estimates tokens. Real P1
    adds textbook method text and proper trimming to the budget."""
    answer = submission.answer_for(problem.id)
    criteria = rubric.for_problem(problem.id)
    work = answer.work_text if answer else ""
    final = answer.final_answer if answer else None
    parts = [problem.statement, problem.reference_solution or "", GRADING_POLICY, work]
    parts += [c.description for c in criteria]
    est = sum(rough_token_estimate(p) for p in parts)
    return GradingContext(
        problem_id=problem.id,
        problem_statement=problem.statement,
        reference_solution=problem.reference_solution or "",
        reference_answer=problem.reference_answer,
        rubric_criteria=criteria,
        grading_policy=GRADING_POLICY,
        student_work=work,
        student_final_answer=final,
        points_possible=problem.points_possible,
        estimated_tokens=est,
    )


def build_submission_context(assignment: Assignment, submission: Submission, rubric: Rubric) -> SubmissionContext:
    """Loop build_context over the assignment's approved problems."""
    problems = {p.id: p for p in assignment.problems}
    contexts = [
        build_context(problems[a.problem_id], submission, rubric)
        for a in submission.answers
    ]
    return SubmissionContext(submission_id=submission.id, problem_contexts=contexts)
