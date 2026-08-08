"""Tests for the P2 grading lane."""

from __future__ import annotations

from lanes import p1_context
from lanes import p2_grading as p2
from contracts import ProblemOutcome
import fixtures as f


def test_p2_full_and_partial_credit():
    submission = f.sample_submission()
    rubric = f.sample_rubric()
    context = f.sample_submission_context()

    grade, trace = p2.grade(submission, rubric, context)

    assert len(grade.problem_grades) == 2
    q1 = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q1)
    q2 = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q2)

    assert q1.outcome is ProblemOutcome.GRADED
    assert q1.points_awarded == 5
    assert q1.answer_matched is True
    assert q1.critic_agreement is True

    assert q2.outcome is ProblemOutcome.GRADED
    assert q2.points_awarded == 2.5
    assert q2.answer_matched is False
    assert q2.partial_credit_reason is not None
    assert q2.critic_agreement is False

    assert trace.stop_reason.value == "completed"
    assert trace.critic_agreement is False


def test_p2_no_answer_outcome():
    submission = f.sample_submission()
    submission.answers[0].final_answer = None
    rubric = f.sample_rubric()
    context = p1_context.build_submission_context(f.sample_assignment(), submission, rubric)

    grade, _ = p2.grade(submission, rubric, context)
    q1 = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q1)

    assert q1.outcome is ProblemOutcome.NO_ANSWER
    assert q1.points_awarded == 0
    assert q1.answer_matched is None
    assert q1.partial_credit_reason is None
