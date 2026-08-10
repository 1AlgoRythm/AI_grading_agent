"""Grading-sanity check (plan §12): three crafted submissions to the same
problem -- a clean correct answer, a partially-right method, and a wrong
method -- must be ranked in the correct order. This is the single cheapest
piece of evidence that the grader's partial credit isn't arbitrary.

Runs through the real pipeline (build_context -> p2.grade), not a mocked
shortcut, so it exercises the actual grader/critic/reconciliation path.
"""
from __future__ import annotations

from uuid import uuid4

from contracts import ArtifactStatus, Assignment, Problem, Rubric, RubricCriterion, Submission, SubmissionAnswer
from lanes import p1_context
from lanes import p2_grading as p2

STATEMENT = "Expand and simplify: (x+1)^2."
REFERENCE_ANSWER = "x^2 + 2x + 1"
REFERENCE_SOLUTION = "(x+1)^2 = (x+1)(x+1) = x^2 + x + x + 1 = x^2 + 2x + 1."


def _assignment_and_rubric():
    assignment = Assignment(label="sanity-hw", title="Sanity HW", type="math")
    problem = Problem(
        assignment_id=assignment.id, label="Q1", statement=STATEMENT, points_possible=5,
        reference_answer=REFERENCE_ANSWER, reference_solution=REFERENCE_SOLUTION,
        solution_status=ArtifactStatus.APPROVED,
    )
    assignment.problems.append(problem)
    rubric = Rubric(
        assignment_id=assignment.id,
        status=ArtifactStatus.APPROVED,
        criteria=[
            RubricCriterion(
                problem_id=problem.id, name="Correct expansion",
                description="Full credit for x^2 + 2x + 1. Partial credit if the square is "
                            "attempted but a term is dropped or mis-added.",
                points=5,
                failure_signals=["dropped the middle term", "treated as x^2 + 1"],
            ),
        ],
    )
    return assignment, problem, rubric


def _grade_for(assignment, problem, rubric, work_text: str, final_answer: str) -> float:
    submission = Submission(
        assignment_id=assignment.id, student_label=f"student-{uuid4().hex[:6]}",
        answers=[SubmissionAnswer(problem_id=problem.id, work_text=work_text, final_answer=final_answer)],
    )
    context = p1_context.build_submission_context(assignment, submission, rubric)
    grade, _ = p2.grade(submission, rubric, context)
    return grade.problem_grades[0].points_awarded


def test_strong_beats_middling_beats_flawed():
    assignment, problem, rubric = _assignment_and_rubric()

    strong = _grade_for(
        assignment, problem, rubric,
        work_text="(x+1)^2 = (x+1)(x+1) = x^2 + x + x + 1 = x^2 + 2x + 1",
        final_answer="x^2 + 2x + 1",
    )
    middling = _grade_for(
        assignment, problem, rubric,
        work_text="I expanded (x+1)^2 but dropped the middle term, so I treated as x^2 + 1",
        final_answer="x^2 + 1",
    )
    flawed = _grade_for(
        assignment, problem, rubric,
        work_text="I guessed based on a different problem that looked similar.",
        final_answer="7",
    )

    assert strong > middling > flawed
    assert strong == problem.points_possible
    assert flawed == 0
