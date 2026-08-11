"""Tests for the P2 grading lane: grader + critic + reconciliation."""

from __future__ import annotations

from uuid import UUID

import pytest

from lanes import p1_context
from lanes import p2_grading as p2
from contracts import GradingContext, ModelError, ProblemOutcome, StepKind, StopReason, rough_token_estimate
import fixtures as f


def test_p2_full_and_partial_credit():
    """Q1 is objectively correct (critic skipped, §5 guard 2); Q2 drops the
    middle term, so the critic first disagrees with the grader's generic
    evidence, forcing one revision that finds a stronger, rubric-grounded
    justification -- bounded reconciliation ending in agreement."""
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
    assert q2.critic_agreement is True

    assert trace.stop_reason is StopReason.COMPLETED
    assert trace.critic_agreement is True
    assert trace.num_revisions == 1
    assert not grade.escalated

    critique_steps = [s for s in trace.steps if s.type == "critique"]
    revision_steps = [s for s in trace.steps if s.type == "revision"]
    assert len(critique_steps) == 2  # initial disagreement + post-revision recheck
    assert len(revision_steps) == 1


def test_p2_matched_answer_skips_the_critic():
    """An objective tool match needs no subjective critique (§5 guard 2):
    the critic never runs, so it costs nothing beyond the tool check."""
    submission = f.sample_submission()
    rubric = f.sample_rubric()
    context = f.sample_submission_context()

    _, trace = p2.grade(submission, rubric, context)

    q1_steps = [s for s in trace.steps if s.data.get("problem") == f.Q1.hex[-2:]]
    assert not any(s.type == "critique" for s in q1_steps)
    assert not any(s.type == "revision" for s in q1_steps)


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


def _bare_context(problem_id: UUID, work_text: str, final_answer: str) -> GradingContext:
    """Build a minimal GradingContext for Q2 with custom shown work, bypassing
    P1 so tests can control the exact text the grader/critic see."""
    problem = next(p for p in f.sample_assignment().problems if p.id == problem_id)
    criteria = f.sample_rubric().for_problem(problem_id)
    parts = [problem.statement, problem.reference_solution or "", f.GRADING_POLICY, work_text]
    return GradingContext(
        problem_id=problem_id,
        problem_statement=problem.statement,
        reference_solution=problem.reference_solution or "",
        reference_answer=problem.reference_answer,
        rubric_criteria=criteria,
        grading_policy=f.GRADING_POLICY,
        student_work=work_text,
        student_final_answer=final_answer,
        points_possible=problem.points_possible,
        estimated_tokens=sum(rough_token_estimate(p) for p in parts),
    )


def test_p2_unresolved_disagreement_escalates():
    """When the grader can't find rubric-grounded or reference-overlapping
    evidence even after a revision, the critic disagrees twice and the whole
    submission is escalated for human review -- the reconciliation loop must
    not ping-pong forever (§5 design guard 1)."""
    submission = f.sample_submission()
    submission.answers[1].work_text = (
        "I tried to expand this using algebra but got confused about the steps."
    )
    submission.answers[1].final_answer = "0"
    rubric = f.sample_rubric()
    context = f.sample_submission_context()
    # Swap in the confused shown work so the context matches the submission.
    context.problem_contexts[1] = _bare_context(f.Q2, submission.answers[1].work_text, "0")

    grade, trace = p2.grade(submission, rubric, context)

    q2 = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q2)
    assert q2.critic_agreement is False
    assert grade.escalated is True
    assert trace.stop_reason is StopReason.ESCALATED
    assert trace.num_revisions == 1
    assert "Escalated" in q2.evidence


def test_p2_revision_logs_a_reason_step_matching_the_final_awarded_points():
    # Regression: after a revision, the trace's only REASON step showed the
    # PRE-revision grader output -- a reviewer inspecting the trace saw a
    # points_awarded that disagreed with what the final ProblemGrade
    # actually stored, with no step explaining the revised number at all.
    submission = f.sample_submission()
    submission.answers[1].work_text = (
        "I tried to expand this using algebra but got confused about the steps."
    )
    submission.answers[1].final_answer = "0"
    rubric = f.sample_rubric()
    context = f.sample_submission_context()
    context.problem_contexts[1] = _bare_context(f.Q2, submission.answers[1].work_text, "0")

    grade, trace = p2.grade(submission, rubric, context)

    q2 = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q2)
    assert trace.num_revisions == 1  # sanity: this scenario does revise

    reason_steps = [
        s for s in trace.steps
        if s.type == StepKind.REASON and s.data.get("problem_id") == str(f.Q2)
    ]
    assert len(reason_steps) == 2
    assert reason_steps[1].data.get("after_revision") is True
    assert reason_steps[1].data["points_awarded"] == q2.points_awarded


def test_p2_batch_grades_concurrently_and_orders_the_review_queue():
    """Stretch (§7/§10): a batch grades under a concurrency cap, preserves
    input order in its results, and surfaces the escalated submission first
    in the review queue."""
    clean_submission = f.sample_submission()
    escalating_submission = f.sample_submission()
    escalating_submission.id = UUID("00000000-0000-0000-0000-0000000000d1")
    escalating_submission.answers[1].work_text = (
        "I tried to expand this using algebra but got confused about the steps."
    )
    escalating_submission.answers[1].final_answer = "0"

    rubric = f.sample_rubric()
    clean_context = f.sample_submission_context()
    escalating_context = f.sample_submission_context()
    escalating_context.submission_id = escalating_submission.id
    escalating_context.problem_contexts[1] = _bare_context(
        f.Q2, escalating_submission.answers[1].work_text, "0"
    )

    results = p2.grade_batch(
        [clean_submission, escalating_submission],
        rubric,
        [clean_context, escalating_context],
        max_concurrency=2,
    )

    assert [r.submission_id for r in results] == [clean_submission.id, escalating_submission.id]
    assert results[0].error is None and not results[0].grade.escalated
    assert results[1].error is None and results[1].grade.escalated

    order = p2.review_queue_order(results)
    assert order[0] == 1  # the escalated submission surfaces first


def test_p2_batch_spend_cap_skips_work_once_the_running_total_is_reached():
    """Plan §15 risk mitigation: 'Spend cap, capped concurrency + backoff.'
    Tokens are the only cost proxy already tracked (Trace.tokens_used), so
    the cap is expressed in tokens. Sequential (max_concurrency=1) so the
    running total is deterministic."""
    from contracts import StopReason, Trace as TraceModel

    def fixed_cost_grade_fn(submission, rubric, context):
        grade = f.sample_grade()
        trace = TraceModel(stop_reason=StopReason.COMPLETED, tokens_used=100, steps=[])
        return grade, trace

    submissions = [f.sample_submission() for _ in range(3)]
    contexts = [f.sample_submission_context() for _ in range(3)]

    results = p2.grade_batch(
        submissions, f.sample_rubric(), contexts,
        max_concurrency=1, max_total_tokens=150, grade_fn=fixed_cost_grade_fn,
    )

    assert [r.skipped for r in results] == [False, False, True]
    assert results[2].error is not None and "spend cap" in results[2].error
    assert results[2].grade is None


def test_p2_batch_without_a_spend_cap_never_skips():
    def fixed_cost_grade_fn(submission, rubric, context):
        return f.sample_grade(), f.sample_trace()

    submissions = [f.sample_submission() for _ in range(3)]
    contexts = [f.sample_submission_context() for _ in range(3)]

    results = p2.grade_batch(submissions, f.sample_rubric(), contexts, grade_fn=fixed_cost_grade_fn)

    assert all(not r.skipped for r in results)
    assert all(r.error is None for r in results)


def test_p2_batch_retries_transient_model_errors_with_backoff():
    """Plan §15 risk mitigation: 'capped concurrency + backoff.' A ModelError
    is transient (a call to the model provider), so it's worth a bounded,
    backed-off retry before the submission is given up on."""
    attempts = {"count": 0}

    def flaky_grade_fn(submission, rubric, context):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ModelError("transient provider hiccup")
        return f.sample_grade(), f.sample_trace()

    results = p2.grade_batch(
        [f.sample_submission()],
        f.sample_rubric(),
        [f.sample_submission_context()],
        max_retries=3,
        base_delay_seconds=0.01,
        grade_fn=flaky_grade_fn,
    )

    assert attempts["count"] == 3
    assert results[0].error is None
    assert results[0].grade is not None


def test_p2_batch_gives_up_after_max_retries_and_isolates_the_failure():
    """One submission that never recovers must not take down the batch or
    retry forever (contracts.py decision 8: per-submission isolation)."""

    def always_fails(submission, rubric, context):
        raise ModelError("provider is down")

    results = p2.grade_batch(
        [f.sample_submission()],
        f.sample_rubric(),
        [f.sample_submission_context()],
        max_retries=2,
        base_delay_seconds=0.01,
        grade_fn=always_fails,
    )

    assert results[0].grade is None
    assert "provider is down" in results[0].error


def test_p2_batch_isolates_an_unexpected_exception_not_just_the_two_known_types():
    # Regression: a pydantic ValidationError (e.g. from a data-shape bug like
    # the points_possible-rounding one fixed elsewhere) is neither a
    # ModelError nor a GradingError. It used to propagate straight out of
    # asyncio.gather() and abort the WHOLE batch, breaking decision 8's
    # documented per-submission isolation guarantee for every other,
    # perfectly good submission alongside it.
    def good(submission, rubric, context):
        return p2.grade_submission(submission, rubric, context)

    def bad(submission, rubric, context):
        raise ValueError("something this one submission's data triggered")

    good_sub, bad_sub = f.sample_submission(), f.sample_submission().model_copy(deep=True)

    def grade_fn(submission, rubric, context):
        return (good if submission is good_sub else bad)(submission, rubric, context)

    results = p2.grade_batch(
        [good_sub, bad_sub],
        f.sample_rubric(),
        [f.sample_submission_context(), f.sample_submission_context()],
        grade_fn=grade_fn,
    )

    assert results[0].grade is not None
    assert results[1].grade is None
    assert "something this one submission's data triggered" in results[1].error


def test_p2_batch_does_not_retry_non_model_grading_errors():
    """A ToolError (e.g. a SymPy crash) will fail the same way on retry --
    only ModelError is worth backing off for."""
    from contracts import ToolError

    attempts = {"count": 0}

    def broken_tool(submission, rubric, context):
        attempts["count"] += 1
        raise ToolError("sympy blew up")

    results = p2.grade_batch(
        [f.sample_submission()],
        f.sample_rubric(),
        [f.sample_submission_context()],
        max_retries=3,
        base_delay_seconds=0.01,
        grade_fn=broken_tool,
    )

    assert attempts["count"] == 1  # no retry
    assert "sympy blew up" in results[0].error


def test_critic_agreement_rate_computes_fraction_across_the_batch():
    fully_agreeing = f.sample_grade()
    results = [p2.BatchResult(submission_id=f.SID, grade=fully_agreeing, trace=f.sample_trace(), error=None)]
    assert p2.critic_agreement_rate(results) == 1.0

    mixed = f.sample_grade()
    mixed.problem_grades[1].critic_agreement = False
    results = [p2.BatchResult(submission_id=f.SID, grade=mixed, trace=f.sample_trace(), error=None)]
    assert p2.critic_agreement_rate(results) == 0.5

    assert p2.critic_agreement_rate([]) is None


def test_check_critic_independence_flags_suspiciously_high_agreement():
    """§5's own design guard: '100% agreement' is the signal the critic isn't
    independent, not a sign everything is fine."""
    grade = f.sample_grade()  # both problem grades critic_agreement=True
    results = [
        p2.BatchResult(submission_id=f.SID, grade=grade, trace=f.sample_trace(), error=None)
        for _ in range(3)  # 3 x 2 problem grades = 6 samples, over the default floor of 5
    ]
    warning = p2.check_critic_independence(results)
    assert warning is not None
    assert "100.0%" in warning
    assert "isn't independent" in warning


def test_check_critic_independence_is_quiet_below_the_sample_floor():
    grade = f.sample_grade()
    results = [p2.BatchResult(submission_id=f.SID, grade=grade, trace=f.sample_trace(), error=None)]
    assert p2.check_critic_independence(results, min_samples=5) is None


def test_check_critic_independence_is_quiet_when_real_disagreement_exists():
    grade = f.sample_grade()
    grade.problem_grades[1].critic_agreement = False
    results = [
        p2.BatchResult(submission_id=f.SID, grade=grade, trace=f.sample_trace(), error=None)
        for _ in range(3)
    ]
    assert p2.check_critic_independence(results, min_samples=5) is None


def test_p2_batch_warns_when_the_whole_batch_agrees_suspiciously_often():
    def always_agrees(submission, rubric, context):
        return f.sample_grade(), f.sample_trace()

    submissions = [f.sample_submission() for _ in range(3)]
    contexts = [f.sample_submission_context() for _ in range(3)]

    with pytest.warns(RuntimeWarning, match="isn't independent"):
        p2.grade_batch(submissions, f.sample_rubric(), contexts, grade_fn=always_agrees)


def _capture_call_model_json(monkeypatch, target_module):
    captured = {}

    def fake_call_model_json(prompt, max_tokens=512, temperature=0.0, model=None):
        captured["temperature"] = temperature
        captured["model"] = model
        return {}  # force the offline fallback so no JSON parsing is needed

    monkeypatch.setattr(target_module, "call_model_json", fake_call_model_json)
    return captured


def test_grader_calls_the_model_provider_deterministically(monkeypatch):
    """The grader must be reproducible run-to-run (plan §13 reliability is a
    tracked evaluation signal), so it always asks for temperature=0.0."""
    from lanes import p2_grader

    captured = _capture_call_model_json(monkeypatch, p2_grader)
    ctx = _bare_context(f.Q1, "2x + 6 = 10, so 2x = 4, therefore x = 2.", "x = 2")

    p2_grader.run_grader(ctx, tool_matched=True)

    assert captured["temperature"] == 0.0
    assert captured["model"] is None


def test_critic_defaults_to_a_hotter_temperature_than_the_grader(monkeypatch):
    """§5: the critic should 'ideally' run on a different model or
    temperature so it isn't just replaying the grader's own reasoning."""
    from lanes import p2_critic, p2_grader

    ctx = _bare_context(f.Q2, "(x + 1)^2 = x^2 + 1.", "x^2 + 1")
    grader_result = p2_grader.run_grader(ctx, tool_matched=False)

    captured = _capture_call_model_json(monkeypatch, p2_critic)
    p2_critic.run_critic(ctx, grader_result)

    assert captured["temperature"] == pytest.approx(0.7)
    assert captured["model"] is None


def test_critic_respects_env_var_overrides_for_model_and_temperature(monkeypatch):
    from lanes import p2_critic, p2_grader

    monkeypatch.setenv("CRITIC_MODEL_NAME", "a-different-model")
    monkeypatch.setenv("CRITIC_TEMPERATURE", "0.3")

    ctx = _bare_context(f.Q2, "(x + 1)^2 = x^2 + 1.", "x^2 + 1")
    grader_result = p2_grader.run_grader(ctx, tool_matched=False)

    captured = _capture_call_model_json(monkeypatch, p2_critic)
    p2_critic.run_critic(ctx, grader_result)

    assert captured["temperature"] == pytest.approx(0.3)
    assert captured["model"] == "a-different-model"


def test_grader_prompt_carries_its_own_previous_proposal_on_revision():
    # Fix: without this, the revision-round grader was handed a critique
    # with no visible referent for its own prior number -- it re-derived
    # from scratch, often reached the same score by the same route, and the
    # critic objected again (the escalation loop this fix targets).
    from lanes import p2_grader

    ctx = _bare_context(f.Q2, "(x + 1)^2 = x^2 + 1.", "x^2 + 1")
    previous = p2_grader.GraderResult(
        points_awarded=2.5, evidence="prior evidence text",
        partial_credit_reason="prior reason", rationale="prior rationale",
    )

    prompt = p2_grader.build_grader_prompt(ctx, tool_matched=False, critique="not specific enough", previous=previous)

    assert "YOUR PREVIOUS PROPOSAL" in prompt
    assert "2.5" in prompt and "prior evidence text" in prompt


def test_grader_prompt_omits_previous_proposal_when_none_given():
    # Every existing caller (first grading pass, and any caller that
    # predates this fix) must see exactly the same prompt as before.
    from lanes import p2_grader

    ctx = _bare_context(f.Q2, "(x + 1)^2 = x^2 + 1.", "x^2 + 1")
    prompt = p2_grader.build_grader_prompt(ctx, tool_matched=False, critique="not specific enough")
    assert "YOUR PREVIOUS PROPOSAL" not in prompt


def test_critic_prompt_carries_its_own_previous_objection_on_recheck():
    # Fix: without this, the recheck critic (handed the grader's revision
    # but not its own prior objection) could restate an already-addressed
    # concern or raise an unrelated new one, guaranteeing escalation even on
    # a grade that was actually fixed.
    from lanes import p2_critic, p2_grader

    ctx = _bare_context(f.Q2, "(x + 1)^2 = x^2 + 1.", "x^2 + 1")
    result = p2_grader.GraderResult(
        points_awarded=2.5, evidence="revised evidence", partial_credit_reason="revised reason",
        rationale="revised rationale",
    )

    prompt = p2_critic.build_critic_prompt(ctx, result, previous_critique="the middle term was dropped")

    assert "YOU RAISED THIS OBJECTION" in prompt
    assert "the middle term was dropped" in prompt
    assert "if your original concern is resolved, agree" in prompt.lower()


def test_critic_prompt_omits_previous_objection_when_none_given():
    from lanes import p2_critic, p2_grader

    ctx = _bare_context(f.Q2, "(x + 1)^2 = x^2 + 1.", "x^2 + 1")
    result = p2_grader.GraderResult(
        points_awarded=2.5, evidence="e", partial_credit_reason="r", rationale="ra",
    )
    prompt = p2_critic.build_critic_prompt(ctx, result)
    assert "YOU RAISED THIS OBJECTION" not in prompt


def test_p2_engine_revision_round_wires_grader_and_critic_memory_through(monkeypatch):
    # Integration-level: lanes/p2_engine.py's revision round must actually
    # pass the grader's prior proposal and the critic's prior objection
    # through, not just have the plumbing exist unused in p2_grader.py/
    # p2_critic.py. Uses _grade_one_problem directly (not the full 2-problem
    # fixture submission) so there's no objectively-matched Q1 grader call
    # to account for -- isolates exactly the revision-round code path.
    from lanes import p2_critic, p2_engine, p2_grader

    ctx = _bare_context(f.Q2, "(x + 1)^2 = x^2 + 1.", "x^2 + 1")
    grader_prompts = []
    critic_prompts = []

    def fake_grader_json(prompt, max_tokens=768, temperature=0.0, model=None):
        grader_prompts.append(prompt)
        return {"points_awarded": 2.5, "evidence": "e", "partial_credit_reason": "r", "rationale": "ra"}

    def fake_critic_json(prompt, max_tokens=512, temperature=0.7, model=None):
        critic_prompts.append(prompt)
        # Disagree on the first call (forces the revision round), agree on the second.
        return {"agrees": len(critic_prompts) > 1, "critique": None if len(critic_prompts) > 1 else "not specific"}

    monkeypatch.setattr(p2_grader, "call_model_json", fake_grader_json)
    monkeypatch.setattr(p2_critic, "call_model_json", fake_critic_json)

    problem_grade, revisions = p2_engine._grade_one_problem(f.Q2, "x^2 + 1", ctx, ctx.points_possible, [])

    assert revisions == 1
    assert len(grader_prompts) == 2  # initial pass + one bounded revision
    assert len(critic_prompts) == 2  # initial critique + one recheck
    assert "YOUR PREVIOUS PROPOSAL" not in grader_prompts[0]
    assert "YOUR PREVIOUS PROPOSAL" in grader_prompts[1]
    assert "YOU RAISED THIS OBJECTION" not in critic_prompts[0]
    assert "YOU RAISED THIS OBJECTION" in critic_prompts[1]
    assert "not specific" in critic_prompts[1]  # the critic's own prior wording, carried forward
