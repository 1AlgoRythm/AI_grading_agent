"""Student-facing portal: enrolled courses/assignments, self-upload,
published grades + feedback + rubric, regrade requests, and the grounded
follow-up chat -- all scoped to st.session_state["user"].id.

Stage 4 of the auth build turns this from Stage 3's single "pick one of
your own graded submissions" screen into the full portal: a home view of
every assignment the student is enrolled in (via lanes/course_storage.py's
courses_for_student/assignments_for_course), each showing "not submitted" /
"submitted -- awaiting grade" / "graded", a self-upload path for the first
state, and the graded view (score/feedback/rubric/chat/regrade) for the
last. A student never sees another student's data anywhere here -- every
lookup is scoped to st.session_state["user"].id or .email.

Run with: ``streamlit run student_app.py`` (no login gate outside the
unified app.py -- standalone runs with no logged-in user show nothing).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional
from uuid import UUID

import streamlit as st

from contracts import ArtifactStatus, Grade, problem_label_map
from lanes import p1_ingestion as p1
from lanes import p2_grading as p2
from lanes.course_storage import CourseStore
from lanes.p1_rag import rehydrate_textbook_from_db, retrieve_method_from_textbook
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store
from lanes.p3_feedback import answer_followup, feedback_history, generate_feedback, register_feedback_context
from lanes.regrade_storage import RegradeStore


def _get_stores() -> tuple[P1Store, P2Store, CourseStore, RegradeStore]:
    db_url = os.getenv("DATABASE_URL", "sqlite:///grading_demo.db")
    if "p1_store" not in st.session_state:
        st.session_state.p1_store = P1Store(db_url)
    if "p2_store" not in st.session_state:
        st.session_state.p2_store = P2Store(db_url)
    if "course_store" not in st.session_state:
        st.session_state.course_store = CourseStore(db_url)
    if "regrade_store" not in st.session_state:
        st.session_state.regrade_store = RegradeStore(db_url)
    return (
        st.session_state.p1_store, st.session_state.p2_store,
        st.session_state.course_store, st.session_state.regrade_store,
    )


def _write_uploaded_file(uploaded) -> str:
    # Same approach p1_app.py's own upload handling uses: a real temp path
    # with the original filename preserved (not NamedTemporaryFile's random
    # name), since ingest_submission derives the student handle from the
    # source's file stem.
    tmp_dir = Path(tempfile.mkdtemp())
    dest = tmp_dir / (uploaded.name or "upload.txt")
    dest.write_bytes(uploaded.getvalue())
    return str(dest)


def _best_grade(grades: list[Grade]) -> Optional[Grade]:
    """Prefer a published grade over a still-unpublished one when a student
    has more than one grade recorded for the same assignment -- "graded"
    must win over "awaiting grade" in the status shown. Stage 5: visibility
    keys on `published` (a soft, still-editable state), not the harder
    APPROVED lock -- finalize() sets both, so an approved grade is always
    published too, but a merely-published one need not be approved yet."""
    if not grades:
        return None
    published = [g for g in grades if g.published]
    return published[0] if published else grades[0]


def _render_upload(p1_store: P1Store, p2_store: P2Store, course_store: CourseStore, user, assignment) -> None:
    rubric = p1_store.load_rubric_for_assignment(assignment.id)
    if rubric is None or rubric.status is not ArtifactStatus.APPROVED:
        st.caption("Your instructor hasn't finished setting up this assignment yet -- check back later.")
        return

    key = str(assignment.id)
    uploaded = st.file_uploader(
        "Your submission file (.txt, .md, .ipynb, .pdf)", type=["txt", "md", "ipynb", "pdf"],
        key=f"upload-file-{key}",
    )
    pasted = st.text_area("...or paste your submission text directly", key=f"upload-text-{key}")

    if st.button("Submit", key=f"upload-submit-{key}", disabled=not (uploaded or pasted.strip())):
        source = _write_uploaded_file(uploaded) if uploaded is not None else pasted
        try:
            submission = p1.ingest_submission(source, assignment=assignment, student_id=user.id)
            context = p1.build_submission_context(assignment, submission, rubric)
            grade, trace = p2.grade(submission, rubric, context)
        except (ValueError, KeyError) as exc:
            # Untrusted, free-form upload -- a parsing/mapping mismatch here
            # is a bad-input case to report clearly, not a raw traceback.
            st.error(f"Could not process your submission: {exc}")
            return
        p2_store.save(grade, trace)
        course_store.record_submission_owner(str(submission.id), user.id, str(assignment.id))
        st.success("Submitted! Your instructor will grade it soon.")
        st.rerun()


def _render_regrade(regrade_store: RegradeStore, user, assignment, grade: Grade) -> None:
    st.subheader("Regrade requests")
    requests_for_grade = [
        r for r in regrade_store.requests_for_student(user.id) if r.grade_id == str(grade.id)
    ]
    for req in requests_for_grade:
        with st.expander(f"Request ({req.status}) -- opened {req.created_at:%Y-%m-%d %H:%M}"):
            for msg in regrade_store.messages_for_request(req.id):
                who = "You" if msg.author_role == "student" else "Instructor"
                st.write(f"**{who}:** {msg.body}")
            if req.status != "closed":
                reply = st.text_area("Reply", key=f"regrade-reply-{req.id}")
                if st.button("Send reply", key=f"regrade-reply-submit-{req.id}"):
                    if not reply.strip():
                        st.error("Message must not be blank.")
                    else:
                        regrade_store.add_message(req.id, author_role="student", author_id=user.id, body=reply)
                        st.rerun()

    new_body = st.text_area("Open a new regrade request", key=f"regrade-new-{grade.id}")
    if st.button("Send regrade request", key=f"regrade-open-{grade.id}"):
        if not new_body.strip():
            st.error("Message must not be blank.")
        else:
            regrade_store.create_request(
                grade_id=str(grade.id), submission_id=str(grade.submission_id),
                assignment_id=str(assignment.id), student_id=user.id, body=new_body,
            )
            st.rerun()


def _render_graded_view(
    p1_store: P1Store, regrade_store: RegradeStore, user, assignment, grade: Grade,
) -> None:
    rubric = p1_store.load_rubric_for_assignment(grade.assignment_id)
    if rubric is None:
        st.error("Could not load this submission's rubric -- ask your instructor.")
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
    st.subheader("Ask a follow-up")
    for past_question, past_answer in feedback_history(grade.submission_id):
        with st.chat_message("user"):
            st.write(past_question)
        with st.chat_message("assistant"):
            st.write(past_answer)

    if question := st.chat_input("e.g. why did I lose a point on Q2?", key=f"chat-{grade.submission_id}"):
        # Retrieved per-question (not reusing whatever was retrieved for the
        # original rubric) -- a follow-up like "why does substitution work
        # here" can legitimately point at different course material than
        # the problem statement did.
        snippet = retrieve_method_from_textbook(question)
        answer_followup(question, grade.submission_id, method_context=snippet)
        st.rerun()

    st.divider()
    _render_regrade(regrade_store, user, assignment, grade)


def render() -> None:
    st.title("AI Grading Agent")
    st.caption("Your courses, assignments, and grades")

    p1_store, p2_store, course_store, regrade_store = _get_stores()
    # Defensive: this could be the first screen to run in a fresh container
    # (a no-op once textbook/ already has content, so cheap to check
    # unconditionally).
    rehydrate_textbook_from_db(p1_store)

    user = st.session_state.get("user")
    if user is None:
        # Reached with no logged-in student -- e.g. a standalone
        # `streamlit run student_app.py` outside app.py's login gate. Fail
        # closed: there is no identity to scope courses/grades to, so show
        # nothing rather than guess or fall back to "everyone's data."
        st.error("You must be logged in as a student to view your portal.")
        return

    courses = course_store.courses_for_student(user.email)
    if not courses:
        st.info("You are not enrolled in any courses yet.")
        return

    grades_by_assignment: dict[str, list[Grade]] = {}
    for g in course_store.grades_for_student(user.id, p2_store):
        grades_by_assignment.setdefault(str(g.assignment_id), []).append(g)

    for course in courses:
        st.header(course.name)
        assignment_ids = course_store.assignments_for_course(course.id)
        if not assignment_ids:
            st.caption("No assignments posted yet.")
            continue

        for assignment_id_str in assignment_ids:
            assignment = p1_store.load_assignment(UUID(assignment_id_str))
            if assignment is None:
                continue
            grade = _best_grade(grades_by_assignment.get(assignment_id_str, []))

            with st.expander(assignment.label, expanded=True):
                if grade is None:
                    st.caption("Status: not submitted")
                    _render_upload(p1_store, p2_store, course_store, user, assignment)
                elif not grade.published:
                    st.caption("Status: submitted -- awaiting grade")
                else:
                    st.caption("Status: graded")
                    _render_graded_view(p1_store, regrade_store, user, assignment, grade)


if __name__ == "__main__":
    st.set_page_config(page_title="AI Grading Agent — Student Portal", layout="wide")
    render()
