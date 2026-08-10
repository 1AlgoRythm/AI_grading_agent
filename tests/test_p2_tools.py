"""The three-state verification verdict (plan §4's VerificationTool seam).

The bug these guard against: collapsing "cannot be checked" into "checked and
wrong", which records a correct proof as incorrect and drives the answer-match
rate to zero on any non-math assignment type.
"""
from __future__ import annotations

import warnings

import pytest

import fixtures as f
from contracts import ArtifactStatus, Assignment, Problem, Rubric, RubricCriterion, Submission, SubmissionAnswer
from lanes import p1_context
from lanes import p2_grading as p2
from lanes.p2_tools import _VERIFIERS, get_verifier, register_verifier, verify_verdict

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


def test_unregistered_type_warns_before_falling_back_to_prose():
    # "proof"/"short_answer" are deliberately mapped to the prose verifier --
    # that's not what this guards. A type nobody registered anything for is
    # different: silently substituting used to mean a new assignment type
    # got graded with no objective check and nobody was told.
    with pytest.warns(RuntimeWarning, match="No verifier registered"):
        get_verifier("some_brand_new_type")


def test_register_verifier_makes_a_new_type_routable():
    class DummyVerifier:
        name = "dummy"

        def verify(self, answer, reference_answer, problem_statement):
            return True

    dummy = DummyVerifier()
    try:
        register_verifier("dummy_type", dummy)
        assert get_verifier("dummy_type") is dummy
        assert get_verifier("DUMMY_TYPE") is dummy  # case-insensitive, like assignment types

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            get_verifier("dummy_type")  # registered -- must not warn
        assert not caught
    finally:
        del _VERIFIERS["dummy_type"]


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


def test_a_mixed_assignment_routes_each_problem_to_its_own_verifier():
    # Before GradingContext.problem_type existed, grade() took exactly one
    # assignment_type for the whole call -- a mixed assignment (one math
    # problem, one proof problem) had no way to give each problem its own
    # verifier in a single grading pass. This is the actual capability fix
    # #7's classifier exists to feed: each GradingContext carries its own
    # detected type, which wins over whatever submission-wide default
    # grade() would otherwise apply to every problem uniformly.
    math_problem_id = f.Q1
    proof_problem_id = f.Q2
    assignment = f.sample_assignment()
    rubric = f.sample_rubric()
    submission = f.sample_submission()
    # Q2's answer becomes a (correct) proof instead of an algebra answer --
    # only the *context*'s declared type controls verifier routing, not the
    # shape of the text itself.
    submission.answers[1].work_text = PROOF
    submission.answers[1].final_answer = PROOF

    context = f.sample_submission_context()
    for i, ctx in enumerate(context.problem_contexts):
        if ctx.problem_id == math_problem_id:
            context.problem_contexts[i] = ctx.model_copy(update={"problem_type": "math"})
        elif ctx.problem_id == proof_problem_id:
            context.problem_contexts[i] = ctx.model_copy(update={
                "problem_type": "proof",
                "reference_answer": PROOF,
                "student_final_answer": PROOF,
            })

    # No assignment_type passed -- if per-problem routing didn't work, both
    # problems would fall back to the same single default ("math"), and the
    # proof would get run through MathVerifier's symbolic equivalence check.
    grade, trace = p2.grade(submission, rubric, context)

    math_grade = next(pg for pg in grade.problem_grades if pg.problem_id == math_problem_id)
    proof_grade = next(pg for pg in grade.problem_grades if pg.problem_id == proof_problem_id)
    assert math_grade.answer_matched is True  # MathVerifier: objectively confirmed
    assert proof_grade.answer_matched is None  # ProseVerifier: no objective check, never False


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
