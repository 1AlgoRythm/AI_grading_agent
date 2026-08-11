"""Tests for P3 feedback, review/audit, and evaluation behavior."""

from uuid import uuid4

import pytest

import fixtures as f
from contracts import ArtifactStatus, GradeResolution, ProblemGrade, ProblemOutcome, now
from lanes import p3_feedback
from lanes.p3_evaluation import evaluate_runs
from lanes.p3_feedback import answer_followup, clear_feedback_contexts, generate_feedback, register_feedback_context
from lanes.p3_review import InMemoryAuditLog, finalize, override_problem_score
from lanes.p3_storage import P3Store


def test_feedback_is_grounded_in_score_and_evidence_not_the_rubric_text():
    # The full rubric criterion (name + description, sometimes carrying raw
    # "[source.txt]"-style tags baked in by draft_rubric) used to be dumped
    # verbatim into student-facing feedback -- too much noise. Feedback
    # should ground itself in the score and the grader's own evidence /
    # partial-credit reason instead, and never repeat the criterion name.
    feedback = generate_feedback(f.sample_grade(), f.sample_rubric())
    assert "5/5" in feedback[f.Q1]
    assert "Correct rearrangement" in feedback[f.Q1]
    assert "Correct isolation and solution" not in feedback[f.Q1]
    assert "2.5/5" in feedback[f.Q2]
    assert "dropped the middle cross term" in feedback[f.Q2]


def test_feedback_never_leaks_the_no_objective_check_placeholder_note():
    # p2_grader.py's offline fallback for answer types with no objective
    # check (proof/short_answer) writes a partial_credit_reason that
    # literally says "no BYOK model is configured" and "Requires human
    # review" -- useful for a reviewer, but internal-process language that
    # must never reach the student-facing feedback panel verbatim.
    grade = f.sample_grade()
    grade.problem_grades[1].partial_credit_reason = (
        "Placeholder score: this problem type has no objective check and no BYOK "
        "model is configured, so no substantive judgment of the reasoning was made. "
        "Requires human review."
    )
    feedback = generate_feedback(grade, f.sample_rubric())
    assert "BYOK" not in feedback[f.Q2]
    assert "Placeholder score" not in feedback[f.Q2]
    assert "Requires human review" not in feedback[f.Q2]


def test_feedback_never_leaks_the_escalation_routing_note():
    # p2_engine.py appends "Escalated to human review after unresolved
    # critic disagreement." to `evidence` purely as an internal routing
    # signal -- it must never reach the student-facing feedback text.
    grade = f.sample_grade()
    grade.problem_grades[0].evidence += " Escalated to human review after unresolved critic disagreement."
    feedback = generate_feedback(grade, f.sample_rubric())
    assert "Escalated" not in feedback[f.Q1]
    assert "critic" not in feedback[f.Q1]


def test_followup_fails_closed_without_registered_context(monkeypatch):
    clear_feedback_contexts()

    def fail_if_called(prompt, max_tokens=512):
        raise AssertionError("must not call the model with no registered context")

    monkeypatch.setattr(p3_feedback, "call_model", fail_if_called)
    assert "not available" in answer_followup("Why did I lose points?", f.SID)


def test_followup_grounds_the_prompt_in_recorded_grade_evidence_not_the_question(monkeypatch):
    # The chat must answer from the recorded grade, not freelance -- ground
    # truth is whatever's actually in generate_feedback's output, so assert
    # on the *prompt* the model sees, not just guess what a real model says.
    register_feedback_context(f.sample_grade(), f.sample_rubric())
    calls = {}

    def fake_call_model(prompt, max_tokens=512):
        calls["prompt"] = prompt
        return "You lost points on problem b2 because the middle term (2x) was dropped."

    monkeypatch.setattr(p3_feedback, "call_model", fake_call_model)

    answer = answer_followup("Why did I lose points on problem b2?", f.SID)

    assert answer == "You lost points on problem b2 because the middle term (2x) was dropped."
    prompt = calls["prompt"]
    assert "Why did I lose points on problem b2?" in prompt
    assert "2.5/5" in prompt  # Q2's recorded evidence
    assert "dropped the middle cross term" in prompt
    assert "ONLY the recorded grading evidence" in prompt  # can't-freelance instruction present


def test_followup_asking_how_it_should_have_been_solved_is_still_grounded_not_freelanced(monkeypatch):
    # generate_feedback's output never states "how a problem should have
    # been solved" -- that's exactly the kind of question the prompt must
    # instruct the model not to freelance an answer for.
    register_feedback_context(f.sample_grade(), f.sample_rubric())
    calls = {}

    def fake_call_model(prompt, max_tokens=512):
        calls["prompt"] = prompt
        return "That's not covered in the recorded grade -- ask your instructor for the worked solution."

    monkeypatch.setattr(p3_feedback, "call_model", fake_call_model)

    answer = answer_followup("How was problem b2 supposed to be solved?", f.SID)

    assert "not covered" in answer
    assert "How was problem b2 supposed to be solved?" in calls["prompt"]


def test_followup_grounded_with_an_assignment_can_answer_how_it_should_have_been_solved(monkeypatch):
    # Fix: without an assignment registered, the chat can't see problem
    # statements or the approved reference solution at all -- "how should I
    # have solved it," the most common student question, was unanswerable
    # even though the answer is sitting right there in
    # problem.reference_solution.
    clear_feedback_contexts()
    assignment = f.sample_assignment()
    register_feedback_context(f.sample_grade(), f.sample_rubric(), assignment)
    calls = {}

    def fake_call_model(prompt, max_tokens=512):
        calls["prompt"] = prompt
        return "Subtract 1, then take the square root."

    monkeypatch.setattr(p3_feedback, "call_model", fake_call_model)

    answer_followup("How was problem b2 supposed to be solved?", f.SID)

    q2 = next(p for p in assignment.problems if p.id == f.Q2)
    assert q2.reference_solution in calls["prompt"]
    assert "Expand and simplify" in calls["prompt"]  # q2.statement, whitespace-normalized by _clean


def test_followup_remembers_earlier_turns_in_the_same_conversation(monkeypatch):
    # Fix: every call used to build a fresh prompt with no conversation
    # memory -- "explain that more simply" has no referent without it.
    clear_feedback_contexts()
    register_feedback_context(f.sample_grade(), f.sample_rubric())
    monkeypatch.setattr(p3_feedback, "call_model", lambda prompt, max_tokens=512: "First answer.")
    answer_followup("Why did I lose points on Q2?", f.SID)

    calls = {}

    def fake_call_model(prompt, max_tokens=512):
        calls["prompt"] = prompt
        return "Second answer."

    monkeypatch.setattr(p3_feedback, "call_model", fake_call_model)
    answer_followup("Explain that more simply.", f.SID)

    assert "Why did I lose points on Q2?" in calls["prompt"]
    assert "First answer." in calls["prompt"]
    assert p3_feedback.feedback_history(f.SID) == [
        ("Why did I lose points on Q2?", "First answer."),
        ("Explain that more simply.", "Second answer."),
    ]


def test_registering_a_different_grade_for_the_same_submission_starts_a_fresh_conversation(monkeypatch):
    # A new grade (a re-grade) for the same submission_id must not inherit
    # the previous grade's transcript -- but re-registering the exact SAME
    # grade (e.g. every Streamlit rerun) must not wipe an in-progress
    # conversation either.
    clear_feedback_contexts()
    grade = f.sample_grade()
    register_feedback_context(grade, f.sample_rubric())
    monkeypatch.setattr(p3_feedback, "call_model", lambda prompt, max_tokens=512: "An answer.")
    answer_followup("A question.", f.SID)
    assert len(p3_feedback.feedback_history(f.SID)) == 1

    # Same grade, re-registered (a Streamlit rerun) -- history survives.
    register_feedback_context(grade, f.sample_rubric())
    assert len(p3_feedback.feedback_history(f.SID)) == 1

    # A genuinely different grade for the same submission -- fresh start.
    regraded = grade.model_copy(deep=True, update={"id": uuid4()})
    register_feedback_context(regraded, f.sample_rubric())
    assert p3_feedback.feedback_history(f.SID) == []


def test_register_feedback_context_rejects_an_assignment_that_doesnt_match_the_grade():
    clear_feedback_contexts()
    from contracts import Assignment

    mismatched = Assignment(label="other", title="Other", type="math")
    with pytest.raises(ValueError, match="assignment does not match"):
        register_feedback_context(f.sample_grade(), f.sample_rubric(), mismatched)


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


def test_override_never_touches_evidence_even_across_repeated_overrides():
    # `evidence` is the AI's original grading evidence. The human's decision
    # has two homes: `partial_credit_reason` and the audit log -- writing the
    # override reason into `evidence` too used to leak straight into the
    # feedback panel and student chat (both read it verbatim), and repeated
    # overrides piled on another note each time. The correct fix is to never
    # touch it at all, not just deduplicate the notes.
    grade, audit = f.sample_grade(), InMemoryAuditLog()
    original_evidence = grade.problem_grades[1].evidence

    override_problem_score(grade, f.Q2, 3.5, "instructor_1", "First reason", audit)
    assert grade.problem_grades[1].evidence == original_evidence
    assert grade.problem_grades[1].partial_credit_reason == "First reason"

    override_problem_score(grade, f.Q2, 4.0, "instructor_1", "Second reason", audit)
    assert grade.problem_grades[1].evidence == original_evidence
    assert grade.problem_grades[1].partial_credit_reason == "Second reason"
    assert "Human override" not in grade.problem_grades[1].evidence

    # The audit log, not evidence, is where both reasons actually live.
    entries = audit.for_grade(grade.id)
    assert [e.reason for e in entries] == ["First reason", "Second reason"]


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


def test_overriding_one_problem_does_not_resolve_a_different_problems_escalation():
    # Regression: override_problem_score used to clear grade.escalated
    # unconditionally, even when a SECOND problem in the same grade was also
    # escalated (critic_agreement=False) and never touched -- finalize()
    # would then approve a grade with a real, unresolved critic
    # disagreement still baked into it.
    grade = f.sample_grade()
    grade.escalated = True
    grade.problem_grades[0].critic_agreement = False  # Q1: untouched
    grade.problem_grades[1].critic_agreement = False  # Q2: about to be overridden
    audit = InMemoryAuditLog()  # one shared log, as a real caller would use

    override_problem_score(grade, f.Q2, 3.5, "instructor_1", "Resolved Q2 on manual review", audit)

    assert grade.escalated is True  # Q1's disagreement is still unresolved
    with pytest.raises(ValueError, match="escalated"):
        finalize(grade, "instructor_1")

    override_problem_score(grade, f.Q1, 5, "instructor_1", "Resolved Q1 on manual review", audit)

    assert grade.escalated is False  # now both are resolved
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


def test_override_is_refused_when_the_grade_belongs_to_a_different_submission():
    # The write-time guard (lanes/active_selection.py is the UI-side half):
    # even if the active-selection sync ever drifts and the UI hands this
    # function a grade that isn't the one currently on screen, the write
    # must be refused outright rather than silently landing on the wrong
    # student's grade. No partial mutation, no audit entry -- a clean
    # refusal before anything is touched.
    grade, audit = f.sample_grade(), InMemoryAuditLog()
    problem_grade_before = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q2).model_copy()
    wrong_submission_id = uuid4()
    assert wrong_submission_id != grade.submission_id

    with pytest.raises(ValueError, match="refusing to override"):
        override_problem_score(
            grade, f.Q2, 5, "instructor_1", "should never be applied", audit,
            expected_submission_id=wrong_submission_id,
        )

    problem_grade_after = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q2)
    assert problem_grade_after == problem_grade_before
    assert audit.for_grade(grade.id) == []

    # The correct submission id is the escape hatch, not a permanent lock:
    # the same call succeeds once the caller passes the grade's own id.
    override_problem_score(
        grade, f.Q2, 5, "instructor_1", "correct submission now", audit,
        expected_submission_id=grade.submission_id,
    )
    assert grade.problem_grades[1].points_awarded == 5


def test_finalize_is_refused_when_the_grade_belongs_to_a_different_submission():
    grade = f.sample_grade()
    grade.escalated = False
    wrong_submission_id = uuid4()
    assert wrong_submission_id != grade.submission_id

    with pytest.raises(ValueError, match="refusing to approve"):
        finalize(grade, "instructor_1", expected_submission_id=wrong_submission_id)
    assert grade.status is not ArtifactStatus.APPROVED
    assert grade.approver_id is None

    finalize(grade, "instructor_1", expected_submission_id=grade.submission_id)
    assert grade.status is ArtifactStatus.APPROVED


def test_override_and_finalize_guards_default_to_not_checking_anything():
    # expected_submission_id defaults to None -- every existing caller
    # (every other test in this file, and every current UI call site that
    # predates this guard) must keep working with zero behavior change.
    grade, audit = f.sample_grade(), InMemoryAuditLog()
    override_problem_score(grade, f.Q2, 5, "instructor_1", "no guard passed", audit)
    assert grade.problem_grades[1].points_awarded == 5
    grade.escalated = False
    finalize(grade, "instructor_1")
    assert grade.status is ArtifactStatus.APPROVED
