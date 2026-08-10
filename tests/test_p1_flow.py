"""Tests for the agentic (no-human-checkpoint) orchestrator.

Mocks the model-calling functions it chains (classify_problem_type,
develop_solution, draft_rubric) to exercise the *intended* behavior
deterministically -- a real model is what makes classification/solution
generation actually work; without one, everything degrades honestly to
low-confidence/empty results (covered separately below), which isn't what
most of these tests are about.
"""
from __future__ import annotations

from contracts import ArtifactStatus
from lanes import p1_flow, p1_solution
from lanes.p1_solution import ProblemTypeClassification


def _fake_classify(math_confident=True):
    def _classify(statement: str) -> ProblemTypeClassification:
        if "prove" in statement.lower():
            return ProblemTypeClassification(type="proof", confident=True)
        return ProblemTypeClassification(type="math", confident=math_confident)
    return _classify


def _fake_call_model_json(response):
    def _fake(prompt, max_tokens=512):
        return response
    return _fake


def test_agentic_flow_develops_and_approves_without_any_human_gate(monkeypatch):
    monkeypatch.setattr(p1_flow, "classify_problem_type", _fake_classify())
    monkeypatch.setattr(
        p1_solution, "call_model_json",
        _fake_call_model_json({"solution": "2x = 4, so x = 2.", "final_answer": "x = 2"}),
    )

    result = p1_flow.process_assignment_agentic("Problem A (5 points): Solve for x: 2x + 6 = 10.")

    problem = result.assignment.problems[0]
    assert problem.solution_status is ArtifactStatus.APPROVED  # the agent's own approval
    assert problem.reference_answer == "x = 2"
    assert result.rubric.status is ArtifactStatus.APPROVED
    assert result.classifications[problem.id].type == "math"
    assert result.classifications[problem.id].confident is True
    assert not result.low_confidence_problem_ids
    assert result.grade is None  # no submission_source given


def test_agentic_flow_grades_a_submission_when_one_is_given(monkeypatch):
    monkeypatch.setattr(p1_flow, "classify_problem_type", _fake_classify())
    monkeypatch.setattr(
        p1_solution, "call_model_json",
        _fake_call_model_json({"solution": "2x = 4, so x = 2.", "final_answer": "x = 2"}),
    )

    result = p1_flow.process_assignment_agentic(
        "Problem A (5 points): Solve for x: 2x + 6 = 10.",
        submission_source="Problem A\nWork: 2x+6=10, so 2x=4, x=2\nFinal answer: x = 2",
    )

    assert result.submission is not None
    assert result.grade is not None
    problem_grade = result.grade.problem_grades[0]
    assert problem_grade.answer_matched is True  # routed to the math verifier, objectively confirmed
    assert problem_grade.points_awarded == problem_grade.points_possible


def test_low_confidence_classification_is_surfaced_not_hidden(monkeypatch):
    # fix #7's spec: an unsure classification routes to the safe judgment-
    # only verifier *and* is surfaced for review -- it must be possible to
    # tell which problems that happened to, not just silently downgrade them.
    monkeypatch.setattr(p1_flow, "classify_problem_type", _fake_classify(math_confident=False))
    monkeypatch.setattr(
        p1_solution, "call_model_json",
        _fake_call_model_json({"solution": "2x = 4, so x = 2.", "final_answer": "x = 2"}),
    )

    result = p1_flow.process_assignment_agentic("Problem A (5 points): Solve for x: 2x + 6 = 10.")

    problem = result.assignment.problems[0]
    assert result.classifications[problem.id].confident is False
    assert problem.id in result.low_confidence_problem_ids


def test_agentic_flow_degrades_honestly_with_no_real_model_configured(monkeypatch):
    # No BYOK key -> classify_problem_type, develop_solution, and draft_rubric
    # all hit the offline stub. The flow must not crash, and must not
    # fabricate confidence it doesn't have.
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)

    result = p1_flow.process_assignment_agentic("Problem A (5 points): Solve for x: 2x + 6 = 10.")

    problem = result.assignment.problems[0]
    assert problem.solution_status is ArtifactStatus.APPROVED  # still "approved" -- by the agent, honestly
    assert result.classifications[problem.id].confident is False
    assert problem.id in result.low_confidence_problem_ids
