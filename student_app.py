"""Student-facing portal: your own grades, feedback, rubric, and follow-up
chat -- nothing else.

Kept on its own screen rather than folded into p3_app.py: p3_app.py is the
instructor's review/override/approve/audit surface (an earlier, explicit
decision removed the chat from there for exactly that reason).

Stage 3 of the auth build scopes this screen to the logged-in student's own
data only (lanes/course_storage.py's ownership records via
`grades_for_student`) -- it used to let anyone pick any assignment and then
any graded submission for it, which is the privacy gap Stage 3 closes. A
student with no owned submissions sees "no graded submissions yet," never
another student's grade.

Run with: ``streamlit run student_app.py`` (no login gate outside the
unified app.py -- standalone runs with no logged-in user show nothing).
"""
from __future__ import annotations

import os

import streamlit as st

from contracts import ArtifactStatus, problem_label_map
from lanes.course_storage import CourseStore
from lanes.p1_rag import rehydrate_textbook_from_db, retrieve_method_from_textbook
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store
from lanes.p3_feedback import answer_followup, feedback_history, generate_feedback, register_feedback_context


def _get_stores() -> tuple[P1Store, P2Store, CourseStore]:
    db_url = os.getenv("DATABASE_URL", "sqlite:///grading_demo.db")
    if "p1_store" not in st.session_state:
        st.session_state.p1_store = P1Store(db_url)
    if "p2_store" not in st.session_state:
        st.session_state.p2_store = P2Store(db_url)
    if "course_store" not in st.session_state:
        st.session_state.course_store = CourseStore(db_url)
    return st.session_state.p1_store, st.session_state.p2_store, st.session_state.course_store


def render() -> None:
    st.title("AI Grading Agent")
    st.caption("Your grades and feedback")

    p1_store, p2_store, course_store = _get_stores()
    # Defensive: this could be the first screen to run in a fresh container
    # (a no-op once textbook/ already has content, so cheap to check
    # unconditionally).
    rehydrate_textbook_from_db(p1_store)

    user = st.session_state.get("user")
    if user is None:
        # Reached with no logged-in student -- e.g. a standalone
        # `streamlit run student_app.py` outside app.py's login gate. Fail
        # closed: there is no identity to scope grades to, so show nothing
        # rather than guess or fall back to "everyone's grades."
        st.error("You must be logged in as a student to view your grades.")
        return

    grades = course_store.grades_for_student(user.id, p2_store)
    if not grades:
        st.info("You have no graded submissions yet.")
        return

    grade_options = {}
    for g in grades:
        assignment = p1_store.load_assignment(g.assignment_id)
        label = assignment.label if assignment is not None else g.assignment_id.hex[-6:]
        tag = "" if g.status is ArtifactStatus.APPROVED else " (awaiting grade)"
        grade_options[f"{label} -- {g.total_awarded:g}/{g.total_possible:g}{tag}"] = g

    choice = st.selectbox("Your submissions", list(grade_options.keys()))
    grade = grade_options[choice]

    if grade.status is not ArtifactStatus.APPROVED:
        # The human-approval gate applies here too: a still-under-review
        # grade isn't final, so there's nothing settled yet to chat about.
        st.info("Submitted -- awaiting your instructor's final grade.")
        return

    assignment = p1_store.load_assignment(grade.assignment_id)
    rubric = p1_store.load_rubric_for_assignment(grade.assignment_id)
    if assignment is None or rubric is None:
        st.error("Could not load this submission's assignment/rubric -- ask your instructor.")
        return

    register_feedback_context(grade, rubric, assignment)
    label_map = problem_label_map(assignment)

    st.metric("Total", f"{grade.total_awarded:g}/{grade.total_possible:g}")

    st.subheader("Feedback")
    for problem_id, text in generate_feedback(grade, rubric).items():
        st.markdown(f"**{label_map.get(problem_id, str(problem_id)[-6:])}**")
        st.write(text)

    st.subheader("Rubric")
    for criterion in rubric.criteria:
        tag = label_map.get(criterion.problem_id, str(criterion.problem_id)[-6:])
        st.write(f"- **{tag} / {criterion.name}** ({criterion.points:g} pts): {criterion.description}")

    st.divider()

    for past_question, past_answer in feedback_history(grade.submission_id):
        with st.chat_message("user"):
            st.write(past_question)
        with st.chat_message("assistant"):
            st.write(past_answer)

    if question := st.chat_input("e.g. why did I lose a point on Q2?"):
        # Retrieved per-question (not reusing whatever was retrieved for the
        # original rubric) -- a follow-up like "why does substitution work
        # here" can legitimately point at different course material than
        # the problem statement did.
        snippet = retrieve_method_from_textbook(question)
        answer_followup(question, grade.submission_id, method_context=snippet)
        st.rerun()


if __name__ == "__main__":
    st.set_page_config(page_title="AI Grading Agent — Student Portal", layout="wide")
    render()
