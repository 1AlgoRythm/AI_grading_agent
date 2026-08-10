"""Tests for P3 feedback, review/audit, and evaluation behavior."""

import pytest

import fixtures as f
from contracts import ArtifactStatus, GradeResolution, ProblemGrade, ProblemOutcome, now
from lanes.p3_evaluation import evaluate_runs
from lanes.p3_feedback import answer_followup, clear_feedback_contexts, generate_feedback, register_feedback_context
from lanes.p3_review import InMemoryAuditLog, finalize, override_problem_score
from lanes.p3_storage import P3Store


def test_feedback_is_grounded_in_score_rubric_and_evidence():
    feedback = generate_feedback(f.sample_grade(), f.sample_rubric())
    assert "5/5" in feedback[f.Q1]
    assert "Correct isolation and solution" in feedback[f.Q1]
    assert "Correct rearrangement" in feedback[f.Q1]
    assert "2.5/5" in feedback[f.Q2]
    assert "dropped the middle cross term" in feedback[f.Q2]


def test_followup_uses_registered_context_and_fails_closed_without_it():
    clear_feedback_contexts()
    assert "not available" in answer_followup("Why did I lose points?", f.SID)
    register_feedback_context(f.sample_grade(), f.sample_rubric())
    answer = answer_followup("Why did I lose points on problem b2?", f.SID)
    assert "Problem b2" in answer and "2.5/5" in answer
    assert "Problem b1" not in answer


def test_override_is_audited_then_finalized_without_losing_provenance():
    grade, audit = f.sample_grade(), InMemoryAuditLog()
    override_problem_score(grade, f.Q2, 3.5, "instructor_1", "Correct expansion setup shown", audit)
    assert grade.problem_grades[1].points_awarded == 3.5
    assert grade.resolution is GradeResolution.HUMAN_OVERRIDDEN
    assert audit.for_grade(grade.id)[0].previous_points == 2.5
    assert audit.for_grade(grade.id)[0].new_points == 3.5
    finalize(grade, "instructor_1")
    assert grade.status is ArtifactStatus.APPROVED
    assert grade.resolution is GradeResolution.HUMAN_OVERRIDDEN


def test_override_requires_reason_and_finalization_rejects_escalation():
    with pytest.raises(ValueError, match="reason"):
        override_problem_score(f.sample_grade(), f.Q2, 3, "instructor_1", "", InMemoryAuditLog())
    grade = f.sample_grade()
    grade.escalated = True
    with pytest.raises(ValueError, match="escalated"):
        finalize(grade, "instructor_1")


def test_overriding_an_escalated_grade_resolves_it_and_finalize_then_succeeds():
    # Previously grade.escalated was set by the grader/critic reconciliation
    # but nothing ever cleared it -- an escalated grade was a permanent dead
    # end, since finalize() always refuses one. An override is the human
    # resolution the escalation was asking for, so it should clear the flag.
    grade = f.sample_grade()
    grade.escalated = True

    override_problem_score(grade, f.Q2, 3.5, "instructor_1", "Resolved on manual review", InMemoryAuditLog())

    assert grade.escalated is False
    finalize(grade, "instructor_1")
    assert grade.status is ArtifactStatus.APPROVED


def test_override_can_move_partial_credit_to_full_credit():
    grade, audit = f.sample_grade(), InMemoryAuditLog()
    override_problem_score(
        grade, f.Q2, 5, "instructor_1", "Equivalent correct method accepted", audit
    )
    assert grade.problem_grades[1].points_awarded == 5
    assert grade.problem_grades[1].partial_credit_reason is None


def test_override_can_grade_a_previous_no_answer():
    grade, audit = f.sample_grade(), InMemoryAuditLog()
    grade.problem_grades[1] = ProblemGrade(
        problem_id=f.Q2, outcome=ProblemOutcome.NO_ANSWER,
        points_awarded=0, points_possible=5, evidence="No answer extracted."
    )
    override_problem_score(
        grade, f.Q2, 2, "instructor_1", "Instructor found valid shown work", audit
    )
    assert grade.problem_grades[1].outcome is ProblemOutcome.GRADED
    assert grade.problem_grades[1].points_awarded == 2


def test_label_free_evaluation_report():
    report = evaluate_runs([(f.sample_grade(), f.sample_trace())])
    assert report.runs == 1
    assert report.answer_match_rate == 0.5
    assert report.grounding_rate == 1.0
    assert report.critic_agreement_rate == 1.0
    assert report.score_standard_deviation == 0.0
    assert report.average_latency_ms == 5200
    assert report.average_tokens_used == 1840


def test_database_store_persists_audits_and_evaluations(tmp_path):
    store = P3Store(f"sqlite:///{tmp_path / 'p3.db'}")
    grade = f.sample_grade()
    override_problem_score(
        grade, f.Q2, 3, "instructor_1", "Accepted equivalent work", store
    )
    entries = store.for_grade(grade.id)
    assert len(entries) == 1
    assert entries[0].new_points == 3

    report = evaluate_runs([(grade, f.sample_trace())])
    run_id = store.save_evaluation(report, now())
    assert run_id == 1
    assert store.evaluation_runs() == [report]
