"""[P2] Grade + trace review screen (plan §10-P2: "UI: grade panel with the
trace and the override control").

This is P2's own demo screen -- distinct from P3's fully audited
approve/override/finalize workflow in `p3_app.py`. Its job is to make the
grader -> critic -> reconciliation trace inspectable (every REASON/ACT/
CRITIQUE/REVISION step p2_engine.py recorded) and to let a reviewer try a
different score on the spot while looking at that trace.

Deliberately out of scope here, because it belongs to P3 (contracts.py
decision 4: status/resolution/approver_id are provenance for P3's audit
log): final approval, `GradeResolution` bookkeeping, and the audited
override log. The control on this screen re-scores a *proposed* grade for
further inspection and re-persists it via P2's own `p2_storage.P2Store`;
approving a grade for real still happens in the P3 review app.

Run with: streamlit run p2_app.py
"""

from __future__ import annotations

import os

import streamlit as st

import fixtures
from contracts import ProblemOutcome, StepKind, round_to_step
from lanes import p2_grading as p2
from lanes.p2_storage import P2Store

STEP_ICONS = {
    StepKind.REASON: "🧠",
    StepKind.ACT: "🔧",
    StepKind.OBSERVE: "👀",
    StepKind.CRITIQUE: "🧑‍⚖️",
    StepKind.REVISION: "✏️",
}


def _initialize_demo() -> None:
    if "p2_store" not in st.session_state:
        st.session_state.p2_store = P2Store(os.getenv("DATABASE_URL", "sqlite:///grading_demo.db"))

    # Prefer a grade already produced this session on the P1 tab (shared
    # st.session_state -- no DB round-trip needed) over a fresh fixture run,
    # and re-sync whenever a *new* one shows up there (by grade.id), so
    # grading a real submission on tab 1 shows up here immediately even if
    # this tab was already open with an older/fixture grade loaded.
    last = st.session_state.get("last_grade")
    if last is not None:
        _, grade, trace = last
        if st.session_state.get("p2_grade_id") != grade.id:
            st.session_state.p2_grade = grade
            st.session_state.p2_trace = trace
            st.session_state.p2_grade_id = grade.id
        return

    if "p2_grade" in st.session_state:
        return

    submission = fixtures.sample_submission()
    rubric = fixtures.sample_rubric()
    context = fixtures.sample_submission_context()
    grade, trace = p2.grade(submission, rubric, context)
    st.session_state.p2_store.save(grade, trace)
    st.session_state.p2_grade = grade
    st.session_state.p2_trace = trace
    st.session_state.p2_grade_id = grade.id


def _render_trace() -> None:
    st.header("Grader + critic trace")
    trace = st.session_state.p2_trace
    st.caption(
        f"stop_reason={trace.stop_reason.value} · critic_agreement={trace.critic_agreement} · "
        f"num_revisions={trace.num_revisions} · tokens={trace.tokens_used} · latency={trace.latency_ms}ms"
    )
    if not trace.steps:
        st.caption("No steps recorded.")
        return
    for i, step in enumerate(trace.steps):
        icon = STEP_ICONS.get(step.type, "•")
        label = f"{icon} {step.type} — problem {step.data.get('problem', '?')}"
        with st.expander(label, expanded=False):
            st.json(step.data)


def _render_problem_panel(index: int) -> None:
    grade = st.session_state.p2_grade
    problem_grade = grade.problem_grades[index]

    st.subheader(f"Problem {problem_grade.problem_id.hex[-2:]}")
    st.write(f"**Outcome:** {problem_grade.outcome.value}")
    st.write(f"**Proposed score:** {problem_grade.points_awarded:g}/{problem_grade.points_possible:g}")
    st.write(f"**Answer matched (tool-checked):** {problem_grade.answer_matched}")
    st.write(f"**Critic agreement:** {problem_grade.critic_agreement}")
    st.write(f"**Evidence:** {problem_grade.evidence or 'No evidence recorded.'}")
    if problem_grade.partial_credit_reason:
        st.write(f"**Partial-credit reason:** {problem_grade.partial_credit_reason}")

    if problem_grade.outcome is not ProblemOutcome.GRADED:
        return  # nothing to re-score on a no-answer / ungradeable outcome

    with st.form(f"p2-rescore-{problem_grade.problem_id}"):
        points = st.number_input(
            "Try a different score (not final — approve in the P3 review app)",
            min_value=0.0,
            max_value=float(problem_grade.points_possible),
            value=float(problem_grade.points_awarded),
            step=0.5,
            key=f"p2-points-{problem_grade.problem_id}",
        )
        reason = st.text_input("Reason", key=f"p2-reason-{problem_grade.problem_id}")
        submitted = st.form_submit_button("Re-score from the trace view")
        if submitted:
            if not reason.strip():
                st.error("A reason is required.")
            else:
                problem_grade.points_awarded = round_to_step(points)
                problem_grade.partial_credit_reason = (
                    reason if points < problem_grade.points_possible else None
                )
                problem_grade.evidence = f"{problem_grade.evidence} Reviewer note: {reason}".strip()
                st.session_state.p2_store.save(grade, st.session_state.p2_trace)
                st.success("Re-scored and persisted to the grades table. Final approval still happens in P3.")
                st.rerun()


def render() -> None:
    _initialize_demo()
    grade = st.session_state.p2_grade

    st.title("AI Grading Agent")
    st.caption("P2 — grader + critic trace review and score re-check")
    st.metric("Total", f"{grade.total_awarded:g}/{grade.total_possible:g}")
    if grade.escalated:
        st.warning(
            "The critic and grader did not reconcile on at least one problem after the bounded "
            "revision round; escalated for human review."
        )

    left, right = st.columns([3, 2])
    with left:
        st.header("Grade panel")
        for index in range(len(grade.problem_grades)):
            _render_problem_panel(index)
    with right:
        _render_trace()


if __name__ == "__main__":
    st.set_page_config(page_title="AI Grading Agent — P2 Trace Review", layout="wide")
    render()
