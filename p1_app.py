"""Streamlit demo for P1 ingestion, solution review, and rubric editing.

Run with: ``streamlit run p1_app.py``

This is the human-approval surface the plan calls for (§10: "upload screen,
rubric editor, solution-review screen") that previously didn't exist -- P1's
functions ran only from `skeleton.py`/tests, with no interface for a human to
actually upload an assignment or approve a solution/rubric. Everything here
persists through `P1Store` so an in-progress review survives a page refresh
or a restart, not just one Python process.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import streamlit as st

from contracts import ArtifactStatus
from lanes import p1_ingestion as p1
from lanes import p1_rag
from lanes.p1_storage import P1Store


def _get_store() -> P1Store:
    if "p1_store" not in st.session_state:
        st.session_state.p1_store = P1Store(os.getenv("P1_DATABASE_URL", "sqlite:///p1_demo.db"))
    return st.session_state.p1_store


def _init_state() -> None:
    st.session_state.setdefault("assignment", None)
    st.session_state.setdefault("method_context", {})
    st.session_state.setdefault("rubric", None)


def _render_upload(store: P1Store) -> None:
    st.header("1. Upload assignment")

    existing = store.list_assignments()
    if existing:
        options = {f"{a.label} ({len(a.problems)} problems)": a for a in existing}
        choice = st.selectbox("Resume a previously saved assignment", ["-- new upload --", *options.keys()])
        if choice != "-- new upload --" and st.button("Load selected assignment"):
            st.session_state.assignment = options[choice]
            st.session_state.method_context = {}
            st.session_state.rubric = None
            st.rerun()

    uploaded = st.file_uploader("Assignment file (.txt, .md, .ipynb, .pdf)", type=["txt", "md", "ipynb", "pdf"])
    pasted = st.text_area("...or paste the assignment text directly", height=150)

    if st.button("Ingest assignment", disabled=not (uploaded or pasted.strip())):
        if uploaded is not None:
            suffix = Path(uploaded.name).suffix or ".txt"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_file.write(uploaded.getvalue())
                source = tmp_file.name
        else:
            source = pasted
        assignment = p1.ingest_assignment(source)
        st.session_state.assignment = assignment
        st.session_state.method_context = {}
        st.session_state.rubric = None
        store.save_assignment(assignment)
        st.success(f"Ingested '{assignment.label}' with {len(assignment.problems)} problem(s).")
        st.rerun()


def _render_solution_review(store: P1Store) -> None:
    assignment = st.session_state.assignment
    st.header("2. Review the proposed solution per problem")

    for problem in assignment.problems:
        approved = problem.solution_status is ArtifactStatus.APPROVED
        with st.expander(f"{problem.label}: {problem.statement[:80]}", expanded=not approved):
            if problem.reference_solution is None:
                if st.button(f"Develop solution for {problem.label}", key=f"dev-{problem.id}"):
                    snippet = st.session_state.method_context.get(problem.id)
                    if snippet is None:
                        snippet = p1.retrieve_method(problem.statement)
                        st.session_state.method_context[problem.id] = snippet
                    p1.develop_solution(problem, method_context=snippet)
                    store.save_assignment(assignment)
                    st.rerun()
                continue

            snippet = st.session_state.method_context.get(problem.id)
            if snippet:
                st.caption(f"Retrieved course method: {snippet[:200]}")

            solution_text = st.text_area(
                "Reference solution", value=problem.reference_solution,
                key=f"sol-{problem.id}", disabled=approved,
            )
            answer_text = st.text_input(
                "Reference final answer", value=problem.reference_answer or "",
                key=f"ans-{problem.id}", disabled=approved,
            )

            ok, note = p1.verify_solution(problem)
            (st.success if ok else st.warning)(f"verify_solution: {note}")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Save edits", key=f"save-{problem.id}", disabled=approved):
                    problem.reference_solution = solution_text
                    problem.reference_answer = answer_text or None
                    store.save_assignment(assignment)
                    st.rerun()
            with col2:
                if approved:
                    st.caption("Approved — solution is locked.")
                elif st.button("Approve solution", key=f"approve-{problem.id}"):
                    problem.reference_solution = solution_text
                    problem.reference_answer = answer_text or None
                    problem.solution_status = ArtifactStatus.APPROVED
                    store.save_assignment(assignment)
                    st.rerun()


def _render_rubric_editor(store: P1Store) -> None:
    assignment = st.session_state.assignment
    all_approved = bool(assignment.problems) and all(
        p.solution_status is ArtifactStatus.APPROVED for p in assignment.problems
    )
    st.header("3. Draft & review the rubric")
    if not all_approved:
        st.info("Approve every problem's solution above before drafting the rubric.")
        return

    if st.session_state.rubric is None:
        if st.button("Draft rubric"):
            rubric = p1.draft_rubric(assignment, method_context=st.session_state.method_context)
            st.session_state.rubric = rubric
            store.save_rubric(rubric)
            st.rerun()
        return

    rubric = st.session_state.rubric
    approved = rubric.status is ArtifactStatus.APPROVED
    problems_by_id = {p.id: p for p in assignment.problems}

    for criterion in rubric.criteria:
        problem = problems_by_id.get(criterion.problem_id)
        label = problem.label if problem else "?"
        with st.expander(f"{label} — {criterion.name}", expanded=not approved):
            criterion.name = st.text_input(
                "Name", value=criterion.name, key=f"cname-{criterion.id}", disabled=approved,
            )
            criterion.description = st.text_area(
                "Description", value=criterion.description, key=f"cdesc-{criterion.id}", disabled=approved,
            )
            criterion.points = st.number_input(
                "Points", value=float(criterion.points), min_value=0.0, step=0.5,
                key=f"cpts-{criterion.id}", disabled=approved,
            )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Save rubric edits", disabled=approved):
            store.save_rubric(rubric)
            st.success("Saved.")
    with col2:
        if approved:
            st.success("Rubric APPROVED — ready for grading.")
        elif st.button("Approve rubric"):
            rubric.status = ArtifactStatus.APPROVED
            store.save_rubric(rubric)
            st.rerun()


def main() -> None:
    st.set_page_config(page_title="AI Grading Agent — P1 Ingestion & Rubric", layout="wide")
    st.title("AI Grading Agent")
    st.caption("P1 — ingestion, retrieval, solution development, and rubric drafting")

    store = _get_store()
    _init_state()

    with st.sidebar:
        st.header("Textbook index")
        if st.button("Sync textbook/ folder to the database"):
            count = p1_rag.sync_textbook_index(store)
            st.success(f"Indexed {count} chunk(s) into the textbook_index table.")
        st.caption(f"{len(store.textbook_chunks())} chunk(s) currently indexed.")

    _render_upload(store)
    if st.session_state.assignment is not None:
        _render_solution_review(store)
        _render_rubric_editor(store)


if __name__ == "__main__":
    main()
