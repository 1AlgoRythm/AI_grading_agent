"""Student-facing feedback chat.

Kept on its own screen rather than folded into p3_app.py: p3_app.py is the
instructor's review/override/approve/audit surface (an earlier, explicit
decision removed the chat from there for exactly that reason). This is the
"separate student portal" that decision called for -- a student picks their
graded submission and asks the follow-up chat why they lost a point, without
seeing any instructor-only controls.

Run with: ``streamlit run student_app.py``
"""
from __future__ import annotations

import os

import streamlit as st

from contracts import ArtifactStatus
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store
from lanes.p3_feedback import answer_followup, feedback_history, register_feedback_context


def _get_stores() -> tuple[P1Store, P2Store]:
    db_url = os.getenv("DATABASE_URL", "sqlite:///grading_demo.db")
    if "p1_store" not in st.session_state:
        st.session_state.p1_store = P1Store(db_url)
    if "p2_store" not in st.session_state:
        st.session_state.p2_store = P2Store(db_url)
    return st.session_state.p1_store, st.session_state.p2_store


def render() -> None:
    st.title("AI Grading Agent")
    st.caption("Student feedback chat — ask why you lost a point on a graded submission")

    p1_store, p2_store = _get_stores()

    assignments = p1_store.list_assignments()
    if not assignments:
        st.info("No assignments in the shared database yet.")
        return

    assignment_options = {f"{a.label} ({len(a.problems)} problems)": a for a in assignments}
    assignment_choice = st.selectbox("Assignment", ["-- choose --", *assignment_options.keys()])
    if assignment_choice == "-- choose --":
        return
    assignment = assignment_options[assignment_choice]

    grades = p2_store.grades_for_assignment(assignment.id)
    if not grades:
        st.info("No graded submissions yet for this assignment.")
        return

    grade_options = {
        f"Submission {g.submission_id.hex[-2:]} -- {g.total_awarded:g}/{g.total_possible:g}": g
        for g in grades
    }
    grade_choice = st.selectbox("Your graded submission", list(grade_options.keys()))
    grade = grade_options[grade_choice]

    if grade.status is not ArtifactStatus.APPROVED:
        # The human-approval gate applies here too: a still-under-review
        # grade isn't final, so there's nothing settled yet to chat about.
        st.info("This grade hasn't been finalized by your instructor yet -- check back after it's approved.")
        return

    rubric = p1_store.load_rubric_for_assignment(assignment.id)
    if rubric is None:
        st.error("No rubric found for this assignment -- ask your instructor.")
        return

    register_feedback_context(grade, rubric, assignment)

    st.metric("Total", f"{grade.total_awarded:g}/{grade.total_possible:g}")
    st.divider()

    for past_question, past_answer in feedback_history(grade.submission_id):
        with st.chat_message("user"):
            st.write(past_question)
        with st.chat_message("assistant"):
            st.write(past_answer)

    if question := st.chat_input("e.g. why did I lose a point on Q2?"):
        answer_followup(question, grade.submission_id)
        st.rerun()


if __name__ == "__main__":
    st.set_page_config(page_title="AI Grading Agent — Student Feedback Chat", layout="wide")
    render()
