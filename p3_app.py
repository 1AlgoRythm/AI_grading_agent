"""Streamlit demo for P3 feedback and human grade review.

Run with: ``streamlit run p3_app.py``
"""

from __future__ import annotations

import streamlit as st

import fixtures
from contracts import ArtifactStatus
from lanes.p3_evaluation import evaluate_runs
from lanes.p3_feedback import answer_followup, generate_feedback, register_feedback_context
from lanes.p3_review import InMemoryAuditLog, finalize, override_problem_score


def _initialize_demo() -> None:
    if "grade" not in st.session_state:
        st.session_state.grade = fixtures.sample_grade()
        st.session_state.rubric = fixtures.sample_rubric()
        st.session_state.trace = fixtures.sample_trace()
        st.session_state.audit_log = InMemoryAuditLog()
        st.session_state.chat = []


def _render_problem_review(index: int) -> None:
    grade = st.session_state.grade
    rubric = st.session_state.rubric
    problem_grade = grade.problem_grades[index]
    criterion = rubric.for_problem(problem_grade.problem_id)

    st.subheader(f"Problem {problem_grade.problem_id.hex[-2:]}")
    st.write(f"**Proposed score:** {problem_grade.points_awarded:g}/{problem_grade.points_possible:g}")
    if criterion:
        st.write(f"**Rubric:** {criterion[0].name} — {criterion[0].description}")
    st.write(f"**Evidence:** {problem_grade.evidence or 'No evidence recorded.'}")
    if problem_grade.partial_credit_reason:
        st.write(f"**Partial-credit reason:** {problem_grade.partial_credit_reason}")

    with st.form(f"override-{problem_grade.problem_id}"):
        points = st.number_input(
            "Override score",
            min_value=0.0,
            max_value=float(problem_grade.points_possible),
            value=float(problem_grade.points_awarded),
            step=0.5,
            key=f"points-{problem_grade.problem_id}",
        )
        reason = st.text_input("Reason for override", key=f"reason-{problem_grade.problem_id}")
        submitted = st.form_submit_button(
            "Save override", disabled=grade.status is ArtifactStatus.APPROVED
        )
        if submitted:
            try:
                override_problem_score(
                    grade, problem_grade.problem_id, points,
                    st.session_state.approver_id, reason,
                    st.session_state.audit_log,
                )
            except (ValueError, KeyError) as exc:
                st.error(str(exc))
            else:
                st.success("Override saved and added to the audit log.")
                st.rerun()


def _render_chat() -> None:
    st.header("Student feedback chat")
    register_feedback_context(st.session_state.grade, st.session_state.rubric)
    for role, message in st.session_state.chat:
        with st.chat_message(role):
            st.write(message)
    question = st.chat_input("Ask why points were awarded or deducted")
    if question:
        response = answer_followup(question, st.session_state.grade.submission_id)
        st.session_state.chat.extend((("user", question), ("assistant", response)))
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="AI Grading Agent — P3 Review", layout="wide")
    _initialize_demo()
    grade = st.session_state.grade
    rubric = st.session_state.rubric

    st.title("AI Grading Agent")
    st.caption("P3 — grounded feedback, human review, audit, and evaluation")
    st.session_state.approver_id = st.text_input("Reviewer ID", value="instructor_1")

    left, right = st.columns([3, 2])
    with left:
        st.header("Grade review")
        st.metric("Total", f"{grade.total_awarded:g}/{grade.total_possible:g}")
        st.write(f"Status: **{grade.status.value}** · Resolution: **{grade.resolution.value}**")
        if grade.escalated:
            st.warning("This grade is escalated and must be resolved before approval.")
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
                st.success("Grade approved.")
                st.rerun()

    with right:
        st.header("Grounded feedback")
        for problem_id, text in generate_feedback(grade, rubric).items():
            st.markdown(f"**Problem {problem_id.hex[-2:]}**")
            st.write(text)

        st.header("Evaluation snapshot")
        report = evaluate_runs([(grade, st.session_state.trace)])
        st.json(report.__dict__)

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

    _render_chat()


if __name__ == "__main__":
    main()
