"""Unified entry point -- one Streamlit app, one deployed service, switching
between P1/P2/P3's screens via a sidebar radio.

Without this, only one lane's screen could ever be deployed at a time (the
Dockerfile could only launch one script), which meant the pipeline's upload
and grading steps were unreachable once deployed -- breaking the §11
Definition of Done, which requires walking the *whole* path (upload through
feedback), not just reviewing a grade someone produced locally beforehand.

Each lane's app keeps its own file and its own `render()` -- this only
dispatches between them. `st.session_state` is shared across all three
automatically (it's one process, one session), which is what lets a rubric
approved on the P1 screen show up on the P2/P3 screens without a DB round
trip (see p1_app.py's `last_grade`/`last_grade_rubric` and p2_app.py's /
p3_app.py's use of them).

Run with: ``streamlit run app.py``
"""
from __future__ import annotations

import os

import streamlit as st

import p1_app
import p2_app
import p3_app
import student_app

PAGES = {
    "Upload & Rubric": p1_app.render,
    "Grade & Trace": p2_app.render,
    "Review & Feedback": p3_app.render,
    "Student Feedback Chat": student_app.render,
}


def main() -> None:
    st.set_page_config(page_title="AI Grading Agent", layout="wide")
    st.sidebar.title("AI Grading Agent")
    page = st.sidebar.radio("Screen", list(PAGES.keys()))
    st.sidebar.divider()
    if not (os.getenv("MODEL_PROVIDER") and os.getenv("MODEL_API_KEY")):
        st.sidebar.warning("No BYOK model configured — running deterministic offline fallbacks.")
    PAGES[page]()


if __name__ == "__main__":
    main()
