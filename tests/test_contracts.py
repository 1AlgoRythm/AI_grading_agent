"""Contract tests. These are what make stub-swapping safe: each real
implementation must keep passing these against the shared fixtures.

Run from inside grading_agent/:   pytest -q
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# make the repo root importable when running pytest from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import contracts as c          # noqa: E402
import fixtures as f           # noqa: E402
from contracts import ArtifactStatus  # noqa: E402
from lanes import p1_ingestion as p1  # noqa: E402
from lanes import p2_grading as p2    # noqa: E402
from lanes import p3_feedback as p3   # noqa: E402


# ---- fixtures satisfy the contract & cross-reference ---------------------- #

def test_fixtures_build_and_cross_reference():
    submission = f.sample_submission()
    context = f.sample_submission_context()
    grade = f.sample_grade()
    assert context.submission_id == submission.id
    assert grade.submission_id == submission.id
    assert grade.total_possible == 10


def test_context_within_budget():
    for ctx in f.sample_submission_context().problem_contexts:
        assert ctx.estimated_tokens <= ctx.token_budget


def test_p1_handoff_context_matches_rubric_and_problems():
    assignment = f.sample_assignment()
    rubric = f.sample_rubric()
    context = f.sample_submission_context()
    problem_ids = {problem.id for problem in assignment.problems}

    assert len(context.problem_contexts) == len(assignment.problems)
    for problem_context in context.problem_contexts:
        assert problem_context.problem_id in problem_ids
        assert problem_context.rubric_criteria
        assert problem_context.points_possible > 0
        assert problem_context.estimated_tokens <= problem_context.token_budget
        assert problem_context.problem_statement
        assert problem_context.reference_solution
        assert problem_context.grading_policy
        assert rubric.for_problem(problem_context.problem_id)


# ---- validators fire on bad data ------------------------------------------ #

def test_partial_credit_requires_reason():
    with pytest.raises(ValidationError):
        c.ProblemGrade(problem_id=f.Q1, points_awarded=3, points_possible=5)


def test_award_cannot_exceed_possible():
    with pytest.raises(ValidationError):
        c.ProblemGrade(problem_id=f.Q1, points_awarded=6, points_possible=5, evidence="x")


def test_non_graded_outcome_awards_zero():
    with pytest.raises(ValidationError):
        c.ProblemGrade(problem_id=f.Q1, outcome=c.ProblemOutcome.NO_ANSWER,
                       points_awarded=2, points_possible=5)


def test_over_budget_context_rejected():
    with pytest.raises(ValidationError):
        c.GradingContext(
            problem_id=f.Q1, problem_statement="a", reference_solution="b",
            reference_answer=None, rubric_criteria=[], grading_policy="p",
            student_work="w", student_final_answer=None, points_possible=5,
            token_budget=1, estimated_tokens=999,
        )


def test_unknown_assignment_type_rejected():
    with pytest.raises(ValidationError):
        c.Assignment(label="x", title="t", type="astrophysics")


def test_approved_grade_needs_approver():
    with pytest.raises(ValidationError):
        c.Grade(submission_id=f.SID, assignment_id=f.AID,
                status=ArtifactStatus.APPROVED)


# ---- rounding is lenient (half-up) ---------------------------------------- #

@pytest.mark.parametrize("value,expected", [
    (2.25, 2.5), (2.24, 2.0), (2.75, 3.0), (2.5, 2.5), (5.0, 5.0), (0.0, 0.0),
])
def test_rounding_half_up(value, expected):
    assert c.round_to_step(value) == expected


# ---- the skeleton path runs end to end ------------------------------------ #

def test_skeleton_runs_end_to_end():
    assignment = p1.ingest_assignment("x")
    for prob in assignment.problems:
        p1.develop_solution(prob)
        prob.solution_status = ArtifactStatus.APPROVED
    rubric = p1.draft_rubric(assignment, {})
    rubric.status = ArtifactStatus.APPROVED
    submission = p1.ingest_submission("x")
    context = p1.build_submission_context(assignment, submission, rubric)

    grade, trace = p2.grade(submission, rubric, context)
    feedback = p3.generate_feedback(grade, rubric)
    p3.finalize(grade, approver_id="instructor_1")

    assert grade.status is ArtifactStatus.APPROVED
    assert grade.approver_id == "instructor_1"
    assert len(feedback) == len(grade.problem_grades)
    assert grade.total_possible == 10
    # Q1 matches -> full marks; Q2 differs -> partial
    assert grade.total_awarded < grade.total_possible
