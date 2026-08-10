"""[P1] Agentic orchestration -- no human checkpoints.

Chains the existing ingest -> classify -> retrieve -> develop -> verify ->
draft-rubric -> build-context -> grade pipeline into one call, with the
agent's own decisions standing as final at every stage. This is a real,
deliberate first version, not a placeholder: a supervised mode that pauses
at the solution and rubric stages for a human to review is a planned,
separate follow-up layered on top of these same per-stage functions. The
fully-autonomous path is built first because it needs none of the
pause/resume machinery a checkpoint-based UI requires -- adding that
machinery to a path that doesn't use it yet would be speculative.

This is a named tradeoff, not an oversight: a wrong solution or rubric here
silently corrupts every grade built on it (this is the exact risk the
project's own human-approval gate exists to prevent in supervised mode).
Nothing here is "the" way to run this system -- it exists to be compared
against the supervised path once that one is built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

from contracts import ArtifactStatus, Assignment, Grade, Rubric, Submission, Trace
from lanes import p2_grading as p2
from lanes.p1_context import build_submission_context
from lanes.p1_io import ingest_assignment, ingest_submission
from lanes.p1_rag import retrieve_method_from_textbook
from lanes.p1_solution import ProblemTypeClassification, classify_problem_type, develop_solution, draft_rubric, verify_solution

__all__ = ["AgenticRunResult", "process_assignment_agentic"]


@dataclass
class AgenticRunResult:
    assignment: Assignment
    rubric: Rubric
    # The full classification (type + confidence), not just the resolved
    # type string -- fix #7's spec is "route unconfident classifications to
    # the safe judgment-only verifier AND surface it for review." Routing
    # happens automatically (the resolved type is what actually reaches
    # GradingContext.problem_type); surfacing requires the caller to be able
    # to see *which* problems were a confident detection versus a fallback.
    classifications: dict[UUID, ProblemTypeClassification] = field(default_factory=dict)
    verification_notes: dict[UUID, tuple[bool, str]] = field(default_factory=dict)
    submission: Optional[Submission] = None
    grade: Optional[Grade] = None
    trace: Optional[Trace] = None

    @property
    def low_confidence_problem_ids(self) -> set[UUID]:
        """Problems whose type was a fallback guess, not a confident
        detection -- worth flagging for a human to double-check even in a
        flow that otherwise has no review checkpoints."""
        return {pid for pid, c in self.classifications.items() if not c.confident}


def process_assignment_agentic(
    assignment_source: str,
    submission_source: Optional[str] = None,
) -> AgenticRunResult:
    """Run the whole pipeline with no human checkpoints.

    Per problem: classify its assignment type (fix #7), retrieve its
    method, develop + self-verify its solution, then approve it -- the
    agent's own approval, not a human's. Then draft and approve the rubric
    the same way. If `submission_source` is given, also ingest it, build its
    context (using each problem's own detected type, so a mixed assignment
    routes correctly), and grade it.

    Escalation is not a human checkpoint imposed by this flow -- it's the
    grading engine's own uncertainty gate (plan §5), and it still applies
    here exactly as it does everywhere else: a `Grade.escalated=True` result
    just means the critic and grader didn't reconcile, and is returned as
    data like everything else, not raised as an error.
    """
    assignment = ingest_assignment(assignment_source)

    classifications: dict[UUID, ProblemTypeClassification] = {}
    verification_notes: dict[UUID, tuple[bool, str]] = {}
    method_context: dict[UUID, Optional[str]] = {}

    for problem in assignment.problems:
        classifications[problem.id] = classify_problem_type(problem.statement)

        snippet = retrieve_method_from_textbook(problem.statement)
        method_context[problem.id] = snippet

        develop_solution(problem, method_context=snippet)
        verification_notes[problem.id] = verify_solution(problem)
        problem.solution_status = ArtifactStatus.APPROVED  # the agent's own approval

    rubric = draft_rubric(assignment, method_context=method_context)
    rubric.status = ArtifactStatus.APPROVED  # the agent's own approval

    result = AgenticRunResult(
        assignment=assignment,
        rubric=rubric,
        classifications=classifications,
        verification_notes=verification_notes,
    )
    if submission_source is None:
        return result

    submission = ingest_submission(submission_source, assignment=assignment)
    problem_types = {pid: c.type for pid, c in classifications.items()}
    context = build_submission_context(assignment, submission, rubric, problem_types=problem_types)
    grade, trace = p2.grade(submission, rubric, context)

    result.submission = submission
    result.grade = grade
    result.trace = trace
    return result
