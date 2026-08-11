"""Streamlit demo for P3 feedback and human grade review.

Run with: ``streamlit run p3_app.py``
"""

from __future__ import annotations

import os
from uuid import UUID

import streamlit as st

import fixtures
from contracts import ArtifactStatus, ProblemOutcome, problem_label_map
from lanes import active_selection
from lanes.course_storage import CourseStore
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store
from lanes.p3_evaluation import evaluate_runs
from lanes.p3_feedback import generate_feedback
from lanes.p3_review import finalize, override_problem_score, publish_grade, reopen_grade
from lanes.p3_storage import P3Store
from lanes.regrade_storage import RegradeStore
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


def _get_course_and_regrade_stores() -> tuple[CourseStore, RegradeStore]:
    # Kept separate from _get_stores() -- that tuple's shape is read by
    # every other call site as (p1_store, p2_store, audit_log); growing it
    # would mean touching all of them for two stores only Stage 5's
    # publish/regrade UI needs.
    db_url = os.getenv("DATABASE_URL", "sqlite:///grading_demo.db")
    if "course_store" not in st.session_state:
        st.session_state.course_store = CourseStore(db_url)
    if "regrade_store" not in st.session_state:
        st.session_state.regrade_store = RegradeStore(db_url)
    return st.session_state.course_store, st.session_state.regrade_store


def _load(grade, rubric, trace, assignment_id) -> None:
    # Route through the shared cache: this is the only place p3_app.py picks
    # up a grade (whether from active_selection, the DB picker below, or the
    # demo fixtures), so this one call covers every entry point.
    grade = shared_grade(grade)
    # The demo-fixtures path passes assignment_id=None -- there's no real DB
    # row behind it, so the active selection's submission_id must be None
    # too, not the fixture's own submission id (which would otherwise look
    # like a real, loadable submission to the assignment-scope lock).
    submission_id = grade.submission_id if assignment_id is not None else None
    active_selection.set_active(assignment_id, submission_id, grade, trace, rubric)
    st.session_state.p3_grade = grade
    st.session_state.p3_rubric = rubric
    st.session_state.p3_trace = trace
    st.session_state.p3_assignment_id = assignment_id


def _mirror_active_into_p3_state() -> None:
    assignment_id, _, grade, trace, rubric = active_selection.get_active()
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
                    reason = active_selection.load_active_from_db(
                        assignment.id, grade.submission_id, p1_store, p2_store,
                    )
                    if reason:
                        st.sidebar.error(reason)
                    else:
                        _mirror_active_into_p3_state()
                        st.rerun()

    if st.sidebar.button("Load demo fixtures instead"):
        _load(fixtures.sample_grade(), fixtures.sample_rubric(), fixtures.sample_trace(), None)
        st.rerun()


def _initialize_demo() -> None:
    _get_stores()

    # active_selection is the single source of truth: re-sync from it on
    # every render (not just the first) so a *new* active selection --
    # graded fresh on P1, or a different submission picked via the data
    # source picker below -- shows up here immediately, even if this tab
    # was already open with something else loaded.
    _, _, active_grade, _, _ = active_selection.get_active()
    if active_grade is not None:
        if st.session_state.get("p3_grade") is None or st.session_state.p3_grade.id != active_grade.id:
            _mirror_active_into_p3_state()
        return

    if "p3_grade" in st.session_state:
        return

    # Nothing active anywhere yet -- prefer a grade already produced on the
    # P1 tab (shared st.session_state, and p1_app stashes the rubric
    # alongside it, so this needs zero DB reads) over a fresh fixture run.
    # In the normal flow P1 already writes directly into active_selection
    # at grading time, so this is mostly a defensive fallback.
    last = st.session_state.get("last_grade")
    rubric = st.session_state.get("last_grade_rubric")
    if last is not None and rubric is not None:
        _, grade, trace = last
        _load(grade, rubric, trace, grade.assignment_id)
        return

    _load(fixtures.sample_grade(), fixtures.sample_rubric(), fixtures.sample_trace(), None)


def _render_problem_review(index: int, label_map: dict) -> None:
    # Score-only display -- overriding a score is a p2_app.py ("grade and
    # trace") action now, not this screen's. Keeping an override form here
    # too meant two places could edit the same grade with two separate
    # audit trails; this screen is read-only review + feedback + approval.
    problem_grade = st.session_state.p3_grade.problem_grades[index]
    tag = label_map.get(problem_grade.problem_id, problem_grade.problem_id.hex[-2:])
    score = f"{problem_grade.points_awarded:g}/{problem_grade.points_possible:g}"
    if problem_grade.outcome is ProblemOutcome.NO_ANSWER:
        st.write(f"**Problem {tag}:** {score} (no answer submitted)")
    elif problem_grade.outcome is ProblemOutcome.UNGRADEABLE:
        st.write(f"**Problem {tag}:** {score} (could not be graded)")
    else:
        st.write(f"**Problem {tag}:** {score}")


def _render_publish_controls(grade, label_map: dict) -> None:
    """Stage 5: `published` is a SOFTER visibility gate than the hard
    APPROVED lock -- the student portal shows a grade once it's published,
    but (unlike `finalize`) the instructor can still reopen and change it.
    Approving still sets `published` too (see `finalize`), so an approved
    grade shows here as locked, not as a separate, contradictory state."""
    if not grade.published:
        if st.button("Publish grade", key=f"publish-{grade.id}"):
            active_assignment_id, active_submission_id, _, _, active_rubric = active_selection.get_active()
            try:
                publish_grade(grade, st.session_state.approver_id, expected_submission_id=active_submission_id)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _, p2_store, _ = _get_stores()
                p2_store.save(grade, st.session_state.p3_trace)
                active_selection.set_active(
                    active_assignment_id, active_submission_id, grade, st.session_state.p3_trace, active_rubric,
                )
                st.success("Grade published -- the student can now see it.")
                st.rerun()
        return

    if grade.status is ArtifactStatus.APPROVED:
        st.caption("Published (locked -- approved).")
        return

    st.caption("Published -- visible to the student, and still editable.")
    with st.expander("Change published grade", expanded=False):
        reason = st.text_input("Reason for the change", key=f"reopen-reason-{grade.id}")
        new_points: dict = {}
        for problem_grade in grade.problem_grades:
            if problem_grade.outcome is not ProblemOutcome.GRADED:
                continue
            tag = label_map.get(problem_grade.problem_id, problem_grade.problem_id.hex[-2:])
            new_points[problem_grade.problem_id] = st.number_input(
                f"{tag} -- new score", min_value=0.0, max_value=float(problem_grade.points_possible),
                value=float(problem_grade.points_awarded), step=0.5,
                key=f"reopen-points-{problem_grade.problem_id}",
            )
        if st.button("Save changes & re-publish", key=f"reopen-save-{grade.id}"):
            active_assignment_id, active_submission_id, _, _, active_rubric = active_selection.get_active()
            try:
                reopen_grade(grade, st.session_state.approver_id, reason, expected_submission_id=active_submission_id)
                for problem_grade in grade.problem_grades:
                    new_value = new_points.get(problem_grade.problem_id)
                    if new_value is not None and new_value != problem_grade.points_awarded:
                        override_problem_score(
                            grade, problem_grade.problem_id, new_value,
                            st.session_state.approver_id, reason, st.session_state.audit_log,
                            expected_submission_id=active_submission_id,
                        )
                publish_grade(grade, st.session_state.approver_id, expected_submission_id=active_submission_id)
            except (ValueError, KeyError) as exc:
                st.error(str(exc))
            else:
                _, p2_store, _ = _get_stores()
                p2_store.save(grade, st.session_state.p3_trace)
                active_selection.set_active(
                    active_assignment_id, active_submission_id, grade, st.session_state.p3_trace, active_rubric,
                )
                st.success("Grade updated and re-published -- the student now sees the new score.")
                st.rerun()


def _render_regrade_queue(p1_store: P1Store, p2_store: P2Store, user) -> None:
    """Stage 5's instructor-facing consumer of Stage 4's student-side
    regrade requests: pending (open) requests first, then resolved/closed.
    Scoped to the logged-in instructor's own courses/assignments -- an
    instructor never sees another instructor's requests."""
    st.header("Regrade requests")
    course_store, regrade_store = _get_course_and_regrade_stores()

    my_courses = course_store.list_courses(instructor_id=user.id)
    my_assignment_ids = {
        assignment_id
        for course in my_courses
        for assignment_id in course_store.assignments_for_course(course.id)
    }
    requests = [r for r in regrade_store.list_requests() if r.assignment_id in my_assignment_ids]
    if not requests:
        st.caption("No regrade requests yet.")
        return

    requests.sort(key=lambda r: (r.status != "open", r.created_at))
    options = {
        f"[{r.status}] {r.created_at:%Y-%m-%d %H:%M} -- submission {r.submission_id[-6:]}": r
        for r in requests
    }
    choice = st.selectbox("Requests (open first)", list(options.keys()), key="regrade-queue-picker")
    request = options[choice]

    for msg in regrade_store.messages_for_request(request.id):
        who = "Student" if msg.author_role == "student" else "You"
        st.write(f"**{who}:** {msg.body}")

    if st.button("Focus this submission above (Grade review)", key=f"regrade-focus-{request.id}"):
        reason = active_selection.load_active_from_db(
            UUID(request.assignment_id), UUID(request.submission_id), p1_store, p2_store,
        )
        if reason:
            st.error(reason)
        else:
            _mirror_active_into_p3_state()
            st.rerun()

    if request.status == "closed":
        st.caption("This request is closed.")
        return

    reply = st.text_area("Reply", key=f"regrade-queue-reply-{request.id}")
    if st.button("Send reply", key=f"regrade-queue-reply-submit-{request.id}"):
        if not reply.strip():
            st.error("Reply must not be blank.")
        else:
            regrade_store.add_message(request.id, author_role="instructor", author_id=user.id, body=reply)
            st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Resolve", key=f"regrade-resolve-{request.id}"):
            regrade_store.set_status(request.id, "resolved")
            st.rerun()
    with col2:
        if st.button("Close", key=f"regrade-close-{request.id}"):
            regrade_store.set_status(request.id, "closed")
            st.rerun()


def render() -> None:
    _initialize_demo()
    _render_data_source_picker()
    grade = st.session_state.p3_grade
    rubric = st.session_state.p3_rubric

    p1_store, _, _ = _get_stores()
    assignment_id = st.session_state.get("p3_assignment_id")
    # Best-effort: a demo-fixture grade has no assignment_id (or none matching
    # a real DB row), so this stays {} and every display site below falls
    # back to the hex tag -- never raises just because the assignment can't
    # be loaded.
    assignment = p1_store.load_assignment(assignment_id) if assignment_id else None
    label_map = problem_label_map(assignment) if assignment else {}

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
            _render_problem_review(index, label_map)

        if st.button(
            "Approve final grade",
            type="primary",
            disabled=grade.status is ArtifactStatus.APPROVED,
        ):
            active_assignment_id, active_submission_id, _, _, active_rubric = active_selection.get_active()
            try:
                finalize(grade, st.session_state.approver_id, expected_submission_id=active_submission_id)
            except ValueError as exc:
                if str(exc).startswith("refusing to approve"):
                    st.error(
                        "This action was blocked because the screen and the grade were out of "
                        "sync — reload the submission and try again."
                    )
                else:
                    st.error(str(exc))
            else:
                _, p2_store, _ = _get_stores()
                p2_store.save(grade, st.session_state.p3_trace)
                active_selection.set_active(
                    active_assignment_id, active_submission_id, grade, st.session_state.p3_trace, active_rubric,
                )
                st.success("Grade approved and re-persisted.")
                st.rerun()

        st.divider()
        _render_publish_controls(grade, label_map)

    with right:
        st.header("Grounded feedback")
        for problem_id, text in generate_feedback(grade, rubric).items():
            st.markdown(f"**{label_map.get(problem_id, problem_id.hex[-2:])}**")
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
                f"Problem {label_map.get(entry.problem_id, entry.problem_id.hex[-2:])}: "
                f"{entry.previous_points:g} → {entry.new_points:g} "
                f"by {entry.approver_id} — {entry.reason}"
            )

    # Stage 5: instructor-only, and only meaningful under the unified
    # app.py's login gate -- `user` is None on a standalone `streamlit run
    # p3_app.py`, where there is no course/instructor context to scope
    # requests to.
    user = st.session_state.get("user")
    if user is not None and user.role == "instructor":
        st.divider()
        p1_store, p2_store, _ = _get_stores()
        _render_regrade_queue(p1_store, p2_store, user)


if __name__ == "__main__":
    st.set_page_config(page_title="AI Grading Agent — P3 Review", layout="wide")
    render()
