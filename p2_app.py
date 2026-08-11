"""[P2] Grade + trace review screen (plan §10-P2: "UI: grade panel with the
trace and the override control").

This is P2's own demo screen -- distinct from P3's approve/finalize workflow
in `p3_app.py`. Its job is to make the grader -> critic -> reconciliation
trace inspectable (every REASON/ACT/CRITIQUE/REVISION step p2_engine.py
recorded) alongside the override control.

The override itself calls the same `override_problem_score` P3 owns, rather
than re-implementing score/evidence mutation here. That used to be a
separate, lighter-weight re-score path scoped to this screen only -- it was
removed because `p2_app.py` and `p3_app.py` share the same database
(`DATABASE_URL`), so a shortcut that skipped the audit log and never cleared
`escalated` didn't just short-change this screen, it wrote a half-resolved
grade straight into the table `p3_app.py` reads from. Final *approval*
(`finalize`) still only happens in the P3 review app; this screen can
override a score and see it audited, but never sets `status`/`approved_at`.

Run with: streamlit run p2_app.py
"""

from __future__ import annotations

import os
from uuid import UUID

import streamlit as st

import fixtures
from contracts import ProblemOutcome, StepKind, problem_label_map
from lanes import active_selection
from lanes import p2_grading as p2
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store
from lanes.p3_review import override_problem_score
from lanes.p3_storage import P3Store
from session_cache import shared_grade

STEP_ICONS = {
    StepKind.REASON: "🧠",
    StepKind.ACT: "🔧",
    StepKind.OBSERVE: "👀",
    StepKind.CRITIQUE: "🧑‍⚖️",
    StepKind.REVISION: "✏️",
}


def _initialize_demo() -> None:
    db_url = os.getenv("DATABASE_URL", "sqlite:///grading_demo.db")
    if "p1_store" not in st.session_state:
        st.session_state.p1_store = P1Store(db_url)
    if "p2_store" not in st.session_state:
        st.session_state.p2_store = P2Store(db_url)
    if "audit_log" not in st.session_state:
        # Same key p3_app.py uses, same db_url -- one shared, persistent
        # audit trail regardless of which screen an override came from.
        st.session_state.audit_log = P3Store(db_url)

    # active_selection (lanes/active_selection.py) is the single source of
    # truth for "which submission is active" across all three screens --
    # p2_grade/p2_trace below are just this screen's read-only mirror of it
    # (kept under their original names since tests/test_p2_app.py reads
    # st.session_state.p2_grade directly). Re-mirrored on every render, not
    # just once, so a *new* active selection (a fresh grade on P1, or a
    # different submission picked in P3) shows up here immediately even if
    # this tab was already open with something else loaded.
    _, _, active_grade, active_trace, _ = active_selection.get_active()
    if active_grade is not None:
        grade = shared_grade(active_grade)
        st.session_state.p2_grade = grade
        st.session_state.p2_trace = active_trace
        return

    if "p2_grade" in st.session_state:
        return

    submission = fixtures.sample_submission()
    rubric = fixtures.sample_rubric()
    context = fixtures.sample_submission_context()
    grade, trace = p2.grade(submission, rubric, context)
    grade = shared_grade(grade)
    st.session_state.p2_store.save(grade, trace)
    st.session_state.p2_grade = grade
    st.session_state.p2_trace = trace
    # Nothing was active anywhere yet -- this screen's own demo bootstrap
    # becomes the shared active selection too, so P1/P3 (if visited next,
    # with nothing of their own loaded either) show the same demo data
    # instead of each independently fabricating a different one.
    active_selection.set_active(grade.assignment_id, submission.id, grade, trace, rubric)


def _step_problem_label(step, label_map: dict) -> str:
    # The trace stores the full UUID string under "problem_id" (p2_engine.py
    # is off-limits here -- this only changes how that already-stored value
    # is displayed). Falls back to the hex tag stored under "problem" if the
    # id is missing, unparseable, or not in this assignment's label map (a
    # demo-fixture trace with no matching assignment in the DB, e.g.).
    raw_id = step.data.get("problem_id")
    if raw_id:
        try:
            return label_map[UUID(raw_id)]
        except (ValueError, KeyError):
            pass
    return step.data.get("problem", "?")


def _render_trace(label_map: dict) -> None:
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
        label = f"{icon} {step.type} — problem {_step_problem_label(step, label_map)}"
        with st.expander(label, expanded=False):
            st.json(step.data)


def _render_problem_panel(index: int, label_map: dict) -> None:
    grade = st.session_state.p2_grade
    problem_grade = grade.problem_grades[index]

    st.subheader(label_map.get(problem_grade.problem_id, problem_grade.problem_id.hex[-2:]))
    st.write(f"**Outcome:** {problem_grade.outcome.value}")
    st.write(f"**Proposed score:** {problem_grade.points_awarded:g}/{problem_grade.points_possible:g}")
    st.write(f"**Answer matched (tool-checked):** {problem_grade.answer_matched}")
    st.write(f"**Critic agreement:** {problem_grade.critic_agreement}")
    st.write(f"**Evidence:** {problem_grade.evidence or 'No evidence recorded.'}")
    if problem_grade.partial_credit_reason:
        st.write(f"**Partial-credit reason:** {problem_grade.partial_credit_reason}")

    if problem_grade.outcome is not ProblemOutcome.GRADED:
        return  # nothing to override on a no-answer / ungradeable outcome

    with st.form(f"p2-override-{problem_grade.problem_id}"):
        points = st.number_input(
            "Override score (final approval still happens in the P3 review app)",
            min_value=0.0,
            max_value=float(problem_grade.points_possible),
            value=float(problem_grade.points_awarded),
            step=0.5,
            key=f"p2-points-{problem_grade.problem_id}",
        )
        reason = st.text_input("Reason for override", key=f"p2-reason-{problem_grade.problem_id}")
        submitted = st.form_submit_button("Save override")
        if submitted:
            active_assignment_id, active_submission_id, _, _, active_rubric = active_selection.get_active()
            try:
                override_problem_score(
                    grade, problem_grade.problem_id, points,
                    st.session_state.approver_id, reason,
                    st.session_state.audit_log,
                    expected_submission_id=active_submission_id,
                )
            except ValueError as exc:
                if str(exc).startswith("refusing to override"):
                    st.error(
                        "This action was blocked because the screen and the grade were out of "
                        "sync — reload the submission and try again."
                    )
                else:
                    st.error(str(exc))
            except KeyError as exc:
                st.error(str(exc))
            else:
                st.session_state.p2_store.save(grade, st.session_state.p2_trace)
                # Mutated in place, but re-set explicitly rather than relying
                # on object identity -- makes the "override commits update
                # the active selection" contract explicit, not incidental.
                active_selection.set_active(
                    active_assignment_id, active_submission_id, grade, st.session_state.p2_trace, active_rubric,
                )
                st.success("Override saved, added to the audit log, and re-persisted.")
                st.rerun()


def render() -> None:
    _initialize_demo()
    grade = st.session_state.p2_grade
    # Best-effort: a demo-fixture grade has no matching row in the DB, so
    # this stays {} and every display site below falls back to the hex tag
    # -- never raises just because the assignment can't be loaded.
    assignment = st.session_state.p1_store.load_assignment(grade.assignment_id)
    label_map = problem_label_map(assignment) if assignment else {}

    st.title("AI Grading Agent")
    st.caption("P2 — grader + critic trace review and score override")
    st.session_state.approver_id = st.text_input("Reviewer ID", value="instructor_1")
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
            _render_problem_panel(index, label_map)
    with right:
        _render_trace(label_map)


if __name__ == "__main__":
    st.set_page_config(page_title="AI Grading Agent — P2 Trace Review", layout="wide")
    render()
