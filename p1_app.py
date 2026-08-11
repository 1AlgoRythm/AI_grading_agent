"""Streamlit demo for P1 ingestion, solution review, rubric editing, and the
handoff into P2 grading.

Run with: ``streamlit run p1_app.py``

This is the human-approval surface the plan calls for (§10: "upload screen,
rubric editor, solution-review screen") that previously didn't exist -- P1's
functions ran only from `skeleton.py`/tests, with no interface for a human to
actually upload an assignment or approve a solution/rubric. Everything here
persists through `P1Store` so an in-progress review survives a page refresh
or a restart, not just one Python process.

The submission-upload section below is the other half of "connect P1's real
uploads to P2": it ingests a real submission, builds the budget-checked
`SubmissionContext`, hands both to P2's `grade()`, and persists the result
through `P2Store` -- all on the same shared `DATABASE_URL` P1/P2/P3 use, so
p2_app.py and p3_app.py can pick the resulting grade straight up.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import streamlit as st

from functools import partial

from contracts import ArtifactStatus, problem_label_map
from lanes import active_selection
from lanes import p1_ingestion as p1
from lanes import p1_rag
from lanes import p2_grading as p2
from lanes.p1_io import _read_pdf_text
from lanes.p1_storage import P1Store
from lanes.p2_engine import grade_submission
from lanes.p2_storage import P2Store
from session_cache import shared_grade


def _write_uploaded_file(uploaded) -> str:
    """Write an uploaded file to a temp path, preserving its original name
    (mkdtemp + real filename) instead of NamedTemporaryFile's random name --
    otherwise ingest_assignment/ingest_submission derive the label/student
    handle from a meaningless "tmpXXXXXXXX" stem."""
    tmp_dir = Path(tempfile.mkdtemp())
    dest = tmp_dir / (uploaded.name or "upload.txt")
    dest.write_bytes(uploaded.getvalue())
    return str(dest)


def _save_textbook_upload(uploaded) -> Path:
    """Persist an uploaded textbook/course-material file into textbook/ so
    it joins the retrieval corpus. Unlike _write_uploaded_file (a scratch
    temp path for one ingest call), this must land in the real, persistent
    textbook/ directory -- that's the only place _list_textbook_sources()
    looks. PDFs are extracted to .md since that function only reads
    .txt/.md (a PDF dropped in directly is silently invisible to it)."""
    textbook_dir = Path("textbook")
    textbook_dir.mkdir(exist_ok=True)
    name = uploaded.name or "upload.txt"
    suffix = Path(name).suffix.lower()

    if suffix == ".pdf":
        tmp_dir = Path(tempfile.mkdtemp())
        tmp_pdf = tmp_dir / name
        tmp_pdf.write_bytes(uploaded.getvalue())
        text = _read_pdf_text(tmp_pdf) or ""
        dest = textbook_dir / f"{Path(name).stem}.md"
        dest.write_text(text, encoding="utf8")
    else:
        dest = textbook_dir / name
        dest.write_bytes(uploaded.getvalue())
    return dest


def _auto_sync_textbook_if_changed(store: P1Store) -> Optional[int]:
    """Sync textbook/ into the DB only when the on-disk corpus actually
    changed since the last sync (mtime+size fingerprint) -- no manual
    button click required, whether the change came from the uploader or a
    file dropped into textbook/ directly, and no needless DB write on every
    single Streamlit rerun when nothing changed. Returns the chunk count
    when a sync actually ran, None on a no-op."""
    sources = p1_rag._list_textbook_sources()
    if not sources:
        return None
    fingerprint = p1_rag._corpus_fingerprint(sources)
    if st.session_state.get("textbook_sync_fingerprint") == fingerprint:
        return None
    count = p1_rag.sync_textbook_index(store)
    st.session_state.textbook_sync_fingerprint = fingerprint
    return count


def _get_store() -> P1Store:
    if "p1_store" not in st.session_state:
        st.session_state.p1_store = P1Store(os.getenv("DATABASE_URL", "sqlite:///grading_demo.db"))
    return st.session_state.p1_store


def _get_p2_store() -> P2Store:
    if "p2_store" not in st.session_state:
        st.session_state.p2_store = P2Store(os.getenv("DATABASE_URL", "sqlite:///grading_demo.db"))
    return st.session_state.p2_store


def _set_active_assignment_scope(assignment_id) -> None:
    """Loading/switching to a different assignment on P1 updates the shared
    active_assignment_id (contracts §"assignment scope lock" -- no screen
    may show a submission that doesn't belong to the active assignment). If
    whatever submission was already active elsewhere (P2/P3) belongs to a
    DIFFERENT assignment, it's cleared rather than left dangling out of
    scope; if it already belongs to this one, it's left alone."""
    _, sub_id, grade, trace, rubric = active_selection.get_active()
    if grade is not None and grade.assignment_id == assignment_id:
        active_selection.set_active(assignment_id, sub_id, grade, trace, rubric)
    else:
        active_selection.set_active(assignment_id, None, None, None, None)


def _init_state() -> None:
    st.session_state.setdefault("assignment", None)
    st.session_state.setdefault("method_context", {})
    st.session_state.setdefault("rubric", None)


def _clear_batch_results() -> None:
    """A previous batch's results table is meaningless once the assignment
    changes (or a fresh rubric is drafted) -- cleared alongside `rubric`
    wherever that already gets reset, so a stale table never lingers on
    screen for the wrong assignment."""
    st.session_state.batch_results = []
    st.session_state.batch_ingest_errors = []
    st.session_state.batch_submissions_by_id = {}
    st.session_state.batch_rubric = None


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
            _clear_batch_results()
            _set_active_assignment_scope(options[choice].id)
            st.rerun()

    uploaded = st.file_uploader("Assignment file (.txt, .md, .ipynb, .pdf)", type=["txt", "md", "ipynb", "pdf"])
    pasted = st.text_area("...or paste the assignment text directly", height=150)
    assignment_type = st.selectbox(
        "Assignment type", ["math", "short_answer", "proof"],
        help="Selects the verification tool. 'math' uses SymPy; the others have no "
             "objective check, so grades lean on the critic and your approval.",
    )

    if st.button("Ingest assignment", disabled=not (uploaded or pasted.strip())):
        if uploaded is not None:
            source = _write_uploaded_file(uploaded)
        else:
            source = pasted
        assignment = p1.ingest_assignment(source, assignment_type)
        st.session_state.assignment = assignment
        st.session_state.method_context = {}
        st.session_state.rubric = None
        _clear_batch_results()
        _set_active_assignment_scope(assignment.id)
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
            # ok is three-state: True/False when the check actually ran and
            # reached a verdict, None when it could not run at all (no
            # model configured, a prose reference, nothing re-derivable).
            # Treating None the same as False here would show a scary
            # warning for "nothing was checked," indistinguishable from an
            # actual disagreement.
            if ok is True:
                st.success(f"verify_solution: {note}")
            elif ok is False:
                st.warning(f"verify_solution: {note}")
            else:
                st.info(f"verify_solution: {note}")

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
            _clear_batch_results()
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


def _activate_batch_row(assignment, submission, grade, trace, rubric) -> None:
    """Same mechanism p3_app.py's data-source picker uses to make a
    submission active everywhere (P2/P3 follow) -- reused here rather than
    inventing a second one."""
    if st.session_state.get("last_grade") is not None and st.session_state.last_grade[1].id == grade.id:
        return  # already active -- avoid re-showing the toast on every rerun
    grade = shared_grade(grade)
    st.session_state.last_grade = (submission, grade, trace)
    st.session_state.last_grade_rubric = rubric
    active_selection.set_active(assignment.id, submission.id, grade, trace, rubric)
    st.success(f"Now showing '{submission.student_label}' as the active submission.")


def _render_batch_results_table(assignment, p2_store: P2Store) -> None:
    results = st.session_state.get("batch_results") or []
    ingest_errors = st.session_state.get("batch_ingest_errors") or []
    if not results and not ingest_errors:
        return

    submissions_by_id = st.session_state.get("batch_submissions_by_id") or {}
    rubric = st.session_state.get("batch_rubric")
    label_map = problem_label_map(assignment)

    st.subheader("Batch grading results")

    rows: list[dict] = []
    # None for a row with nothing to activate (an ingest/grading error); a
    # (submission, grade, trace) tuple otherwise.
    row_targets: list[Optional[tuple]] = []

    for name, error in ingest_errors:
        rows.append({
            "Submission": name, "Grade": f"ERROR: {error}",
            "Escalation needed": "—", "Question(s) needing escalation": "",
        })
        row_targets.append(None)

    # review_queue_order (lanes/p2_batch.py) already ranks errored first,
    # then escalated, then by revision count -- exactly the "escalation-
    # needed rows on top" ordering this table requires. Reused as-is.
    for index in p2.review_queue_order(results):
        result = results[index]
        submission = submissions_by_id.get(result.submission_id)
        name = submission.student_label if submission is not None else str(result.submission_id)
        if result.grade is None:
            rows.append({
                "Submission": name,
                "Grade": result.error if result.skipped else f"ERROR: {result.error}",
                "Escalation needed": "—", "Question(s) needing escalation": "",
            })
            row_targets.append(None)
            continue

        grade = result.grade
        escalated_labels = [
            label_map.get(pg.problem_id, pg.problem_id.hex[-2:])
            for pg in grade.problem_grades if pg.critic_agreement is False
        ]
        rows.append({
            "Submission": name,
            "Grade": f"{grade.total_awarded:g}/{grade.total_possible:g}",
            "Escalation needed": "Yes" if grade.escalated else "No",
            "Question(s) needing escalation": ", ".join(escalated_labels),
        })
        row_targets.append((submission, grade, result.trace) if submission is not None else None)

    selected_index: Optional[int] = None
    try:
        event = st.dataframe(
            rows, hide_index=True, on_select="rerun", selection_mode="single-row",
            key="batch_results_table",
        )
        selected_rows = (getattr(event, "selection", None) or {}).get("rows", []) if event else []
        if not selected_rows:
            # Depending on the installed Streamlit version, the selection
            # lives on the returned event object OR on the widget's own
            # session_state entry -- checked defensively rather than
            # assuming one shape.
            state = st.session_state.get("batch_results_table") or {}
            selected_rows = (state.get("selection") or {}).get("rows", [])
        if selected_rows:
            selected_index = selected_rows[0]
    except TypeError:
        # Older Streamlit without st.dataframe row-selection support --
        # same table, plus a plain selectbox to choose the active row.
        st.dataframe(rows, hide_index=True)
        options = ["-- choose --"] + [f"{i}: {r['Submission']}" for i, r in enumerate(rows)]
        choice = st.selectbox("Make a submission active", options, key="batch_results_selectbox")
        if choice != "-- choose --":
            selected_index = int(choice.split(":", 1)[0])

    if selected_index is not None and 0 <= selected_index < len(row_targets) and row_targets[selected_index]:
        submission, grade, trace = row_targets[selected_index]
        _activate_batch_row(assignment, submission, grade, trace, rubric)


def _render_submission_and_grading(p2_store: P2Store) -> None:
    """The other half of the P1 -> P2 handoff: ingest a real submission,
    build its budget-checked context, hand both to P2's grade(), and persist
    the result via P2Store -- so p2_app.py/p3_app.py can pick it straight up
    by assignment_id/submission_id, instead of only ever seeing fixtures."""
    assignment = st.session_state.assignment
    rubric = st.session_state.rubric
    st.header("4. Upload a submission & grade it")
    if rubric is None or rubric.status is not ArtifactStatus.APPROVED:
        st.info("Approve the rubric above before grading a submission.")
        return

    uploaded = st.file_uploader(
        "Submission file(s) (.txt, .md, .ipynb, .pdf)", type=["txt", "md", "ipynb", "pdf"],
        key="submission-file", accept_multiple_files=True,
    )
    pasted = st.text_area("...or paste the submission text directly", height=150, key="submission-text")

    if st.button("Ingest & grade submission", disabled=not (uploaded or pasted.strip())):
        # One or many files is a batch of 1..N either way -- the paste box is
        # just the N=1 case with no file attached. Each source is ingested
        # independently so one bad file becomes an error row below, not a
        # crash that takes the rest of the batch down with it.
        if uploaded:
            sources = [(f.name, _write_uploaded_file(f)) for f in uploaded]
        else:
            sources = [("pasted submission", pasted)]

        submissions: list = []
        contexts: list = []
        ingest_errors: list[tuple[str, str]] = []
        for name, source in sources:
            try:
                submission = p1.ingest_submission(source, assignment=assignment)
                context = p1.build_submission_context(assignment, submission, rubric)
            except (ValueError, KeyError) as exc:
                # Untrusted, free-form upload -- a parsing/mapping mismatch
                # here is a bad-input case to report as an error row, not a
                # reason to crash the app (or the rest of the batch) with a
                # raw traceback.
                ingest_errors.append((name, str(exc)))
            else:
                submissions.append(submission)
                contexts.append(context)

        results = []
        if submissions:
            # grade_batch's `grade_fn` only ever calls fn(submission, rubric,
            # context) -- it has no assignment_type parameter to forward, so
            # passing grade_submission directly would silently grade every
            # non-math assignment as "math". Binding assignment.type here
            # uses grade_batch's own documented override point instead of
            # touching lanes/p2_batch.py.
            results = p2.grade_batch(
                submissions, rubric, contexts,
                grade_fn=partial(grade_submission, assignment_type=assignment.type),
            )
            for result in results:
                if result.grade is not None and result.trace is not None:
                    p2_store.save(result.grade, result.trace)

        st.session_state.batch_results = results
        st.session_state.batch_ingest_errors = ingest_errors
        st.session_state.batch_submissions_by_id = {s.id: s for s in submissions}
        st.session_state.batch_rubric = rubric

        # A lone submission (a file, or the paste box) behaves exactly like
        # the old single-submission path always did: it becomes active
        # immediately, no click required. A real multi-submission batch
        # instead waits for an explicit row pick (requirement 4) -- with
        # several submissions graded at once there's no single obvious
        # "the" one to activate.
        if len(results) == 1 and not ingest_errors and results[0].grade is not None:
            submission = st.session_state.batch_submissions_by_id[results[0].submission_id]
            grade, trace = results[0].grade, results[0].trace
            grade = shared_grade(grade)
            st.session_state.last_grade = (submission, grade, trace)
            st.session_state.last_grade_rubric = rubric
            active_selection.set_active(assignment.id, submission.id, grade, trace, rubric)
            st.success(
                f"Graded '{submission.student_label}': {grade.total_awarded:g}/{grade.total_possible:g} "
                f"({grade.fraction:.0%}). Persisted grade {grade.id} for submission {submission.id}."
            )
        elif ingest_errors and not results:
            st.error(f"Could not grade: {ingest_errors[0][1]}")
        else:
            st.success(f"Graded {len(results)} submission(s); see the results table below.")
        st.rerun()

    _render_batch_results_table(assignment, p2_store)

    last = st.session_state.get("last_grade")
    if last is not None:
        submission, grade, trace = last
        st.subheader(f"Last graded: {submission.student_label}")
        st.write(
            f"**Total:** {grade.total_awarded:g}/{grade.total_possible:g} · "
            f"**Status:** {grade.status.value} · **Escalated:** {grade.escalated} · "
            f"**Trace:** stop={trace.stop_reason.value}, revisions={trace.num_revisions}"
        )
        st.caption(f"grade_id={grade.id} · submission_id={submission.id} · open this in p2_app.py / p3_app.py")

    history = p2_store.grades_for_assignment(assignment.id)
    if history:
        st.subheader(f"Previously graded submissions for '{assignment.label}'")
        for g in history:
            col1, col2 = st.columns([4, 1])
            with col1:
                st.write(f"submission {g.submission_id}: grade {g.id} — {g.total_awarded:g}/{g.total_possible:g}")
            with col2:
                # Makes this submission the active one everywhere (P2/P3
                # follow), not just something you can read about here.
                if st.button("View", key=f"view-hist-{g.id}"):
                    reason = active_selection.load_active_from_db(assignment.id, g.submission_id, store, p2_store)
                    if reason:
                        st.error(reason)
                    else:
                        st.rerun()


def render() -> None:
    st.title("AI Grading Agent")
    st.caption("P1 — ingestion, retrieval, solution development, and rubric drafting")

    store = _get_store()
    p2_store = _get_p2_store()
    _init_state()

    with st.sidebar:
        st.header("Textbook index")
        uploaded_textbook = st.file_uploader(
            "Add course material (.txt, .md, .pdf)", type=["txt", "md", "pdf"], key="textbook-upload",
        )
        if uploaded_textbook is not None and st.session_state.get("last_textbook_upload") != uploaded_textbook.name:
            dest = _save_textbook_upload(uploaded_textbook)
            st.session_state.last_textbook_upload = uploaded_textbook.name
            st.success(f"Saved {dest.name} to textbook/.")

        synced_count = _auto_sync_textbook_if_changed(store)
        if synced_count is not None:
            st.caption(f"Auto-synced: indexed {synced_count} chunk(s).")
        st.caption(f"{len(store.textbook_chunks())} chunk(s) currently indexed.")

    _render_upload(store)
    if st.session_state.assignment is not None:
        _render_solution_review(store)
        _render_rubric_editor(store)
        _render_submission_and_grading(p2_store)


if __name__ == "__main__":
    st.set_page_config(page_title="AI Grading Agent — P1 Ingestion & Rubric", layout="wide")
    render()
