"""The three-state verification verdict (plan §4's VerificationTool seam).

The bug these guard against: collapsing "cannot be checked" into "checked and
wrong", which records a correct proof as incorrect and drives the answer-match
rate to zero on any non-math assignment type.
"""
from __future__ import annotations

import pytest

from contracts import ArtifactStatus, Assignment, Problem, Rubric, RubricCriterion, Submission, SubmissionAnswer
from lanes import p1_context
from lanes import p2_grading as p2
from lanes.p2_tools import get_verifier, verify_verdict

PROOF = "Exchanging the first interval of any optimal solution for the earliest-finishing one preserves feasibility and does not reduce the count."


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)


def test_math_still_confirms_and_contradicts():
    assert verify_verdict("x = 2", "x = 2", "Solve for x: 2x + 6 = 10", "math") is True
    assert verify_verdict("x^2 + 1", "x^2 + 2x + 1", "Expand: (x+1)^2", "math") is False


def test_prose_answer_is_unverifiable_not_wrong():
    assert verify_verdict(PROOF, PROOF, "Prove the greedy choice is safe.", "proof") is None
    assert verify_verdict("anything", "a written argument about exchange", "Explain.", "math") is None


def test_prose_verifier_is_selected_by_type():
    assert get_verifier("proof").name == "none_prose"
    assert get_verifier("short_answer").name == "none_prose"
    assert get_verifier("math").name == "sympy_math"
    assert get_verifier("unregistered").name == "none_prose"


def _proof_setup():
    assignment = Assignment(label="clrs-hw", title="Greedy", type="proof")
    problem = Problem(
        assignment_id=assignment.id, label="Q1",
        statement="Prove that the earliest-finish-time greedy choice is safe.",
        points_possible=5, reference_answer=PROOF, reference_solution=PROOF,
        solution_status=ArtifactStatus.APPROVED,
    )
    assignment.problems.append(problem)
    rubric = Rubric(
        assignment_id=assignment.id, status=ArtifactStatus.APPROVED,
        criteria=[RubricCriterion(
            problem_id=problem.id, name="Exchange argument",
            description="States the exchange, shows feasibility is preserved, and concludes optimality.",
            points=5,
        )],
    )
    submission = Submission(
        assignment_id=assignment.id, student_label="clrs-student",
        answers=[SubmissionAnswer(problem_id=problem.id, work_text=PROOF, final_answer=PROOF)],
    )
    return assignment, rubric, submission


def test_a_proof_is_never_recorded_as_objectively_wrong():
    assignment, rubric, submission = _proof_setup()
    context = p1_context.build_submission_context(assignment, submission, rubric)
    grade, trace = p2.grade(submission, rubric, context, assignment.type)

    problem_grade = grade.problem_grades[0]
    assert problem_grade.answer_matched is None, "unverifiable must be None, never False"
    assert problem_grade.critic_agreement is not None, "the critic must run when there is no tool verdict"
    assert any(step.data.get("verdict") == "not_applicable" for step in trace.steps)


def test_an_unjudged_placeholder_score_escalates_rather_than_silently_standing():
    # Offline (no BYOK key), a proof gets a placeholder half-credit score
    # with no real judgment behind it. The critic must disagree with that
    # placeholder -- rubber-stamping it would let an unexamined score
    # silently become the final grade instead of routing to human review.
    assignment, rubric, submission = _proof_setup()
    context = p1_context.build_submission_context(assignment, submission, rubric)
    grade, trace = p2.grade(submission, rubric, context, assignment.type)

    problem_grade = grade.problem_grades[0]
    assert problem_grade.critic_agreement is False
    assert grade.escalated is True
    assert trace.num_revisions == 1
