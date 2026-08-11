"""Streamlit demo for P3 feedback and human grade review.

Run with: ``streamlit run p3_app.py``
"""

from __future__ import annotations

import os

import streamlit as st

import fixtures
from contracts import ArtifactStatus, ProblemOutcome
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store
from lanes.p3_evaluation import evaluate_runs
from lanes.p3_feedback import generate_feedback
from lanes.p3_review import finalize
from lanes.p3_storage import P3Store
from session_cache import shared_grade


def _get_stores() -> tuple[P1Store, P2Store, P3Store]:
    # Each store gets its own independent guard -- p1_app.py/p2_app.py set
    # `p1_store`/`p2_store` under these exact same keys (shared on purpose,
    # one connection per store type per session under the unified app.py).
    # Bundling all three behind one `"p1_store" not in st.session_state`
    # check meant that once p1_app initialized p1_store first, this whole
    # block -- including audit_log, which nothing else sets -- got skipped.
    db_url = os.getenv("DATABASE_URL", "sqlite:///grading_demo.db")
    if "p1_store" not in st.session_state:
        st.session_state.p1_store = P1Store(db_url)
    if "p2_store" not in st.session_state:
        st.session_state.p2_store = P2Store(db_url)
    if "audit_log" not in st.session_state:
        st.session_state.audit_log = P3Store(db_url)
    return st.session_state.p1_store, st.session_state.p2_store, st.session_state.audit_log


def _load(grade, rubric, trace, assignment_id) -> None:
    # Route through the shared cache: this is the only place p3_app.py picks
    # up a grade (whether from last_grade, the DB picker below, or the demo
    # fixtures), so this one call covers every entry point.
    grade = shared_grade(grade)
    st.session_state.p3_grade = grade
    st.session_state.p3_rubric = rubric
    st.session_state.p3_trace = trace
    st.session_state.p3_assignment_id = assignment_id


def _render_data_source_picker() -> None:
    p1_store, p2_store, _ = _get_stores()
    st.sidebar.header("Data source")

    assignments = p1_store.list_assignments()
    if not assignments:
        st.sidebar.caption("No assignments in the shared database yet -- upload one via p1_app.py.")
    else:
        assignment_options = {f"{a.label} ({len(a.problems)} problems)": a for a in assignments}
        assignment_choice = st.sidebar.selectbox(
            "Assignment", ["-- choose --", *assignment_options.keys()], key="assignment_choice",
        )
        if assignment_choice != "-- choose --":
            assignment = assignment_options[assignment_choice]
            grades = p2_store.grades_for_assignment(assignment.id)
            if not grades:
                st.sidebar.caption("No graded submissions yet for this assignment -- grade one via p2_app.py or p1_app.py.")
            else:
                grade_options = {
                    f"Submission {g.submission_id.hex[-2:]} -- {g.total_awarded:g}/{g.total_possible:g}": g
                    for g in grades
                }
                grade_choice = st.sidebar.selectbox("Graded submission", list(grade_options.keys()))
                if st.sidebar.button("Load this submission"):
                    grade = grade_options[grade_choice]
                    rubric = p1_store.load_rubric_for_assignment(assignment.id)
                    trace = p2_store.get_trace(grade.id)
                    if rubric is None or trace is None:
                        st.sidebar.error("Missing rubric or trace for this grade -- check the P1/P2 data.")
                    else:
                        _load(grade, rubric, trace, assignment.id)
                        st.rerun()

    if st.sidebar.button("Load demo fixtures instead"):
        _load(fixtures.sample_grade(), fixtures.sample_rubric(), fixtures.sample_trace(), None)
        st.rerun()


def _initialize_demo() -> None:
    _get_stores()
    if "p3_grade" in st.session_state:
        return

    # First load this session, nothing explicitly picked yet -- prefer a
    # grade already produced on the P1 tab (shared st.session_state, and
    # p1_app stashes the rubric alongside it, so this needs zero DB reads)
    # over a fresh fixture run.
    last = st.session_state.get("last_grade")
    rubric = st.session_state.get("last_grade_rubric")
    if last is not None and rubric is not None:
        _, grade, trace = last
        _load(grade, rubric, trace, grade.assignment_id)
        return

    _load(fixtures.sample_grade(), fixtures.sample_rubric(), fixtures.sample_trace(), None)


def _render_problem_review(index: int) -> None:
    # Score-only display -- overriding a score is a p2_app.py ("grade and
    # trace") action now, not this screen's. Keeping an override form here
    # too meant two places could edit the same grade with two separate
    # audit trails; this screen is read-only review + feedback + approval.
    problem_grade = st.session_state.p3_grade.problem_grades[index]
    tag = problem_grade.problem_id.hex[-2:]
    score = f"{problem_grade.points_awarded:g}/{problem_grade.points_possible:g}"
    if problem_grade.outcome is ProblemOutcome.NO_ANSWER:
        st.write(f"**Problem {tag}:** {score} (no answer submitted)")
    elif problem_grade.outcome is ProblemOutcome.UNGRADEABLE:
        st.write(f"**Problem {tag}:** {score} (could not be graded)")
    else:
        st.write(f"**Problem {tag}:** {score}")


def render() -> None:
    _initialize_demo()
    _render_data_source_picker()
    grade = st.session_state.p3_grade
    rubric = st.session_state.p3_rubric

    st.title("AI Grading Agent")
    st.caption("P3 — grounded feedback, human review, audit, and evaluation")
    st.session_state.approver_id = st.text_input("Reviewer ID", value="instructor_1")

    left, right = st.columns([3, 2])
    with left:
        st.header("Grade review")
        st.metric("Total", f"{grade.total_awarded:g}/{grade.total_possible:g}")
        st.write(f"Status: **{grade.status.value}** · Resolution: **{grade.resolution.value}**")
        if grade.escalated:
            st.warning("Escalated — resolve it with an override in the P2 grade-and-trace app, then approve here.")
        for index in range(len(grade.problem_grades)):
            _render_problem_review(index)

        if st.button(
            "Approve final grade",
            type="primary",
            disabled=grade.status is ArtifactStatus.APPROVED,
        ):
            try:
                finalize(grade, st.session_state.approver_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _, p2_store, _ = _get_stores()
                p2_store.save(grade, st.session_state.p3_trace)
                st.success("Grade approved and re-persisted.")
                st.rerun()

    with right:
        st.header("Grounded feedback")
        for problem_id, text in generate_feedback(grade, rubric).items():
            st.markdown(f"**Problem {problem_id.hex[-2:]}**")
            st.write(text)

        st.header("Evaluation snapshot")
        report = evaluate_runs([(grade, st.session_state.p3_trace)])
        st.json(report.__dict__)

        if st.session_state.get("p3_assignment_id"):
            st.subheader("Across all graded submissions for this assignment")
            _, p2_store, _ = _get_stores()
            batch_runs = [
                (g, t) for g in p2_store.grades_for_assignment(st.session_state.p3_assignment_id)
                if (t := p2_store.get_trace(g.id)) is not None
            ]
            if batch_runs:
                st.json(evaluate_runs(batch_runs).__dict__)
            else:
                st.caption("No trace data found for this assignment's graded submissions.")

        st.header("Override audit")
        audit_entries = st.session_state.audit_log.for_grade(grade.id)
        if not audit_entries:
            st.caption("No overrides recorded.")
        for entry in audit_entries:
            st.write(
                f"Problem {entry.problem_id.hex[-2:]}: "
                f"{entry.previous_points:g} → {entry.new_points:g} "
                f"by {entry.approver_id} — {entry.reason}"
            )


if __name__ == "__main__":
    st.set_page_config(page_title="AI Grading Agent — P3 Review", layout="wide")
    render()
