"""Single source of truth for "which assignment + submission is active"
across all three screens (p1_app.py, p2_app.py, p3_app.py).

Before this existed, p2_app.py always followed whichever submission was
most recently graded on the P1 tab (st.session_state.last_grade), with no
way to follow a *different* submission picked through p3_app.py's own
data-source picker -- so an instructor could browse to one student's
submission in P3, switch to P2 intending to override that same submission,
and silently be editing a different student's grade instead, with nothing
on screen indicating the two tabs had drifted apart. Routing every screen
through set_active()/get_active()/load_active_from_db() here makes there
exactly one "current submission" everywhere, and lets every write call site
(lanes/p3_review.py's override_problem_score/finalize) pass it as a guard
so a write physically cannot land on the wrong submission even if some
future change reintroduces drift in the sync itself.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import streamlit as st

from contracts import Grade, Rubric, Trace


def set_active(
    assignment_id: Optional[UUID],
    submission_id: Optional[UUID],
    grade: Optional[Grade],
    trace: Optional[Trace],
    rubric: Optional[Rubric],
) -> None:
    st.session_state.active_assignment_id = assignment_id
    st.session_state.active_submission_id = submission_id
    st.session_state.active_grade = grade
    st.session_state.active_trace = trace
    st.session_state.active_rubric = rubric


def get_active() -> tuple[
    Optional[UUID], Optional[UUID], Optional[Grade], Optional[Trace], Optional[Rubric]
]:
    return (
        st.session_state.get("active_assignment_id"),
        st.session_state.get("active_submission_id"),
        st.session_state.get("active_grade"),
        st.session_state.get("active_trace"),
        st.session_state.get("active_rubric"),
    )


def load_active_from_db(assignment_id: UUID, submission_id: UUID, p1_store, p2_store) -> Optional[str]:
    """Load and activate one specific submission's grade/trace/rubric from
    the database. Returns None on success, or a human-readable reason on
    failure -- never raises. A submission that was graded but is somehow
    missing its trace or rubric row is a data gap worth reporting clearly,
    not a crash.
    """
    grades = [g for g in p2_store.grades_for_submission(submission_id) if g.assignment_id == assignment_id]
    if not grades:
        return "No grade found for this submission under the selected assignment."
    grade = grades[-1]
    trace = p2_store.get_trace(grade.id)
    if trace is None:
        return "This submission's grade has no recorded trace."
    rubric = p1_store.load_rubric_for_assignment(assignment_id)
    if rubric is None:
        return "No rubric found for this submission's assignment."
    set_active(assignment_id, submission_id, grade, trace, rubric)
    return None
