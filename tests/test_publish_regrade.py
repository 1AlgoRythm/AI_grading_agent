"""Stage 5 of the auth build: publish-but-not-locked grades
(lanes/p3_review.py's publish_grade/reopen_grade) and the instructor
regrade-request queue (p3_app.py) that consumes Stage 4's student-side
RegradeStore.

Reuses the existing finalize/override_problem_score functions and the
Stage 2-4 course/ownership/regrade plumbing as-is -- this only adds
publish_grade/reopen_grade, Grade.published(_at), RegradeStore.list_requests,
and the p3_app.py UI wired on top of them.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import fixtures as f
from contracts import ArtifactStatus, Grade
from lanes.p3_review import InMemoryAuditLog, finalize, override_problem_score, publish_grade, reopen_grade
from lanes.regrade_storage import RegradeStore

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")
STUDENT_APP_PATH = str(Path(__file__).resolve().parent.parent / "student_app.py")

_ADMIN_EMAIL = "admin@local"
_ADMIN_PASSWORD = "changeme123"


# ---- publish_grade: visible, not locked ----------------------------------- #

def test_publish_grade_sets_published_without_approving_or_locking():
    grade = f.sample_grade()
    assert grade.published is False

    publish_grade(grade, "instructor_1")

    assert grade.published is True
    assert grade.published_at is not None
    assert grade.status is not ArtifactStatus.APPROVED  # not locked
    assert grade.approver_id == "instructor_1"


def test_publish_grade_requires_escalation_resolved_first():
    grade = f.sample_grade()
    grade.escalated = True
    with pytest.raises(ValueError, match="escalated"):
        publish_grade(grade, "instructor_1")


def test_publish_grade_requires_a_non_blank_approver_id():
    grade = f.sample_grade()
    with pytest.raises(ValueError, match="approver_id"):
        publish_grade(grade, "   ")


def test_publish_grade_guards_expected_submission_id():
    grade = f.sample_grade()
    with pytest.raises(ValueError, match="refusing to publish"):
        publish_grade(grade, "instructor_1", expected_submission_id=uuid4())


def test_finalize_also_publishes_the_grade():
    # Approving is strictly stronger than publishing -- a finalized grade
    # must stay visible to the student under the published-based visibility
    # rule, not regress to invisible.
    grade = f.sample_grade()
    finalize(grade, "instructor_1")
    assert grade.status is ArtifactStatus.APPROVED
    assert grade.published is True


def test_grade_still_constructs_without_the_published_fields():
    grade = Grade(submission_id=f.SID, assignment_id=f.AID)
    assert grade.published is False
    assert grade.published_at is None


# ---- reopen -> override -> re-publish ------------------------------------- #

def test_reopen_a_published_grade_override_and_republish_shows_the_new_score():
    grade = f.sample_grade()
    publish_grade(grade, "instructor_1")
    problem_id = grade.problem_grades[1].problem_id  # Q2: 2.5/5, a real change to make
    audit_log = InMemoryAuditLog()

    reopen_grade(grade, "instructor_1", "student appealed, re-checked the work")
    override_problem_score(
        grade, problem_id, grade.problem_grades[1].points_possible,
        "instructor_1", "student appealed, re-checked the work", audit_log,
    )
    publish_grade(grade, "instructor_1")

    assert grade.published is True
    assert grade.status is not ArtifactStatus.APPROVED  # reopening never locks it
    assert grade.problem_grades[1].points_awarded == grade.problem_grades[1].points_possible
    assert len(audit_log.for_grade(grade.id)) == 1
    assert audit_log.for_grade(grade.id)[0].reason == "student appealed, re-checked the work"


def test_reopen_requires_the_grade_to_already_be_published():
    grade = f.sample_grade()
    with pytest.raises(ValueError, match="only a published grade can be reopened"):
        reopen_grade(grade, "instructor_1", "a reason")


def test_reopen_refuses_an_approved_locked_grade():
    grade = f.sample_grade()
    finalize(grade, "instructor_1")
    with pytest.raises(ValueError, match="locked"):
        reopen_grade(grade, "instructor_1", "a reason")


def test_reopen_requires_a_non_blank_reason():
    grade = f.sample_grade()
    publish_grade(grade, "instructor_1")
    with pytest.raises(ValueError, match="reason"):
        reopen_grade(grade, "instructor_1", "   ")


def test_reopen_guards_expected_submission_id():
    grade = f.sample_grade()
    publish_grade(grade, "instructor_1")
    with pytest.raises(ValueError, match="refusing to reopen"):
        reopen_grade(grade, "instructor_1", "a reason", expected_submission_id=uuid4())


# ---- RegradeStore.list_requests (instructor-side lookup) ------------------ #

def test_list_requests_filters_by_status_and_assignment(tmp_path):
    store = RegradeStore(f"sqlite:///{tmp_path / 'app.db'}")
    r1 = store.create_request(
        grade_id="g1", submission_id="s1", assignment_id="a1", student_id="student-a", body="Please recheck Q1.",
    )
    r2 = store.create_request(
        grade_id="g2", submission_id="s2", assignment_id="a2", student_id="student-b", body="Please recheck Q2.",
    )
    store.set_status(r2.id, "resolved")

    assert {r.id for r in store.list_requests()} == {r1.id, r2.id}
    assert [r.id for r in store.list_requests(status="open")] == [r1.id]
    assert [r.id for r in store.list_requests(assignment_id="a2")] == [r2.id]
    assert store.list_requests(status="closed") == []


# ---- End-to-end through the real UI --------------------------------------- #

def _register(at, *, role: str, name: str, email: str, password: str) -> None:
    at.radio[0].set_value("Register").run()
    at.selectbox(key="reg-role").select(role).run()
    at.text_input(key="reg-name").set_value(name).run()
    at.text_input(key="reg-email").set_value(email).run()
    at.text_input(key="reg-password").set_value(password).run()
    at.button(key="register-submit").click().run()
    at.radio[0].set_value("Log in").run()


def _login(at, email: str, password: str) -> None:
    at.text_input(key="login-email").set_value(email).run()
    at.text_input(key="login-password").set_value(password).run()
    at.button(key="login-submit").click().run()


def _login_as_instructor(at, *, email: str = "prof@example.com", password: str = "pw12345") -> None:
    _register(at, role="instructor", name="Prof", email=email, password=password)
    _login(at, _ADMIN_EMAIL, _ADMIN_PASSWORD)
    next(b for b in at.button if b.label == "Approve").click().run()
    next(b for b in at.button if b.label == "Log out").click().run()
    _login(at, email, password)


def _setup_course_assignment_and_grade(at, *, student_email: str) -> None:
    """Instructor session: register+enroll the student, ingest+approve an
    assignment/rubric, link it to a course, and grade one submission
    assigned to that student. Leaves the grade PROPOSED/unpublished."""
    at.sidebar.text_input(key="new-course-name").set_value("Algebra").run()
    at.sidebar.button(key="create-course").click().run()
    at.sidebar.text_input(key="enroll-email").set_value(student_email).run()
    at.sidebar.button(key="enroll-submit").click().run()

    at.text_area[0].set_value("HW\n\nProblem A (5 points): Solve for x: 2x + 6 = 10.").run()
    next(b for b in at.button if b.label == "Ingest assignment").click().run()
    at.sidebar.button(key="link-assignment-course").click().run()
    next(b for b in at.button if b.label.startswith("Develop solution for")).click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve solution").click().run()
    next(b for b in at.button if b.label == "Draft rubric").click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve rubric").click().run()

    assign_box = next(
        sb for sb in at.selectbox if sb.label == "Assign this submission to an enrolled student (optional)"
    )
    assign_box.select(student_email).run()
    next(ta for ta in at.text_area if ta.label == "...or paste the submission text directly").set_value(
        "Problem A\nFinal answer: x = 2"
    ).run(timeout=30)
    next(b for b in at.button if b.label == "Ingest & grade submission").click().run(timeout=30)


def _resolve_escalation_if_any(db_url: str, grade_id) -> None:
    # The critic's offline simulation can occasionally escalate even a
    # correct answer (the same pre-existing grading-pipeline non-determinism
    # worked around in Stage 4's privacy test) -- this test has no business
    # depending on that. Resolve it directly (confirming the existing score
    # counts as "reviewed") so it can proceed to what it's actually about:
    # the Publish/reopen/regrade-queue UI, which correctly refuses to
    # publish an unresolved escalation.
    from lanes.p2_storage import P2Store
    from lanes.p3_review import InMemoryAuditLog, override_problem_score

    p2_store = P2Store(db_url)
    grade = p2_store.get_grade(grade_id)
    if not grade.escalated:
        return
    trace = p2_store.get_trace(grade_id)
    audit_log = InMemoryAuditLog()
    for pg in grade.problem_grades:
        if pg.critic_agreement is False:
            override_problem_score(
                grade, pg.problem_id, pg.points_awarded, "instructor_1",
                "resolving escalation for test setup", audit_log,
            )
    p2_store.save(grade, trace)


def test_end_to_end_publish_reopen_and_regrade_queue_flow(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from streamlit.testing.v1 import AppTest
    from lanes.p2_storage import P2Store

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="student", name="Maya", email="maya@uni.edu", password="pw12345")
    _login_as_instructor(at)
    _setup_course_assignment_and_grade(at, student_email="maya@uni.edu")

    grade_id = at.session_state["last_grade"][1].id
    _resolve_escalation_if_any(db_url, grade_id)

    # --- Publish (not approve) via the P3 review screen --------------------
    at.sidebar.radio[0].set_value("Review & Feedback").run()
    assignment_choice = next(o for o in at.sidebar.selectbox[0].options if o != "-- choose --")
    at.sidebar.selectbox[0].select(assignment_choice).run()
    at.sidebar.selectbox[1].select(at.sidebar.selectbox[1].options[0]).run()
    next(b for b in at.sidebar.button if b.label == "Load this submission").click().run()

    # st.success(...) here is immediately followed by st.rerun() (the same
    # established pattern as p1_app.py's "Ingest assignment") -- the toast
    # is already gone by the time .run() returns, so assert on the
    # persisted effect instead.
    publish_btn = next(b for b in at.button if b.label == "Publish grade")
    publish_btn.click().run()
    assert not at.exception
    assert not at.error

    p2_store = P2Store(db_url)
    published_grade = p2_store.get_grade(grade_id)
    assert published_grade.published is True
    assert published_grade.status is not ArtifactStatus.APPROVED  # not locked

    # --- Student sees the published-but-unlocked grade ----------------------
    # Read-only check through the real login flow (proven to work: see
    # test_student_app.py's dispatcher test).
    at_maya = AppTest.from_file(APP_PATH)
    at_maya.run()
    _login(at_maya, "maya@uni.edu", "pw12345")
    assert any("graded" in c.value.lower() and "awaiting" not in c.value.lower() for c in at_maya.caption)
    assert "5/5" in at_maya.metric[0].value

    # Student opens a regrade request -- via student_app.py directly with an
    # injected session (the pattern tests/test_student_app.py and
    # tests/test_student_portal.py already use successfully): driving this
    # interaction through APP_PATH's real login form reliably corrupts
    # AppTest's widget-state tracking for every subsequent .run() on THAT
    # instance (reproducible even with zero prior interactions -- a
    # student_app.py-specific AppTest quirk, not an app bug), so this
    # sidesteps it rather than fighting it.
    from lanes.auth_storage import UserStore
    maya = UserStore(db_url).get_user_by_email("maya@uni.edu")
    at_maya_portal = AppTest.from_file(STUDENT_APP_PATH)
    at_maya_portal.session_state["user"] = maya
    at_maya_portal.run()
    at_maya_portal.text_area(key=f"regrade-new-{grade_id}").set_value(
        "I think Q1 deserves a closer look."
    ).run()
    next(b for b in at_maya_portal.button if b.label == "Send regrade request").click().run()
    assert not at_maya_portal.exception

    regrade_store = RegradeStore(db_url)
    requests = regrade_store.requests_for_student(maya.id)
    assert len(requests) == 1
    request_id = requests[0].id
    assert requests[0].status == "open"

    # --- Instructor's pending queue: reply, change the grade, resolve ------
    # `at` hasn't re-run since publishing, so its tree still predates the
    # regrade request just created via a separate AppTest instance --
    # re-run to pick up the fresh DB state (the queue re-queries on every
    # render, same as the rest of this screen).
    at.run()
    request_box = next(sb for sb in at.selectbox if sb.label == "Requests (open first)")
    assert any("[open]" in o for o in request_box.options)
    request_box.select(request_box.options[0]).run()
    assert any("I think Q1 deserves a closer look." in w.value for w in at.markdown)

    next(b for b in at.button if b.label == "Focus this submission above (Grade review)").click().run()
    assert not at.exception

    at.text_area(key=f"regrade-queue-reply-{request_id}").set_value("Good catch -- fixing it now.").run()
    next(b for b in at.button if b.label == "Send reply").click().run()
    assert not at.exception

    # Change the published grade via the audited reopen -> override -> publish
    # path -- an actual score change (5 -> 4), not a no-op re-publish.
    at.text_input(key=f"reopen-reason-{grade_id}").set_value("Re-checked Q1 after student's regrade request").run()
    q1_problem_id = published_grade.problem_grades[0].problem_id
    at.number_input(key=f"reopen-points-{q1_problem_id}").set_value(4.0).run()
    next(b for b in at.button if b.label == "Save changes & re-publish").click().run()
    assert not at.exception

    next(b for b in at.button if b.label == "Resolve").click().run()
    assert not at.exception

    assert regrade_store.get_request(request_id).status == "resolved"
    updated_grade = p2_store.get_grade(grade_id)
    assert updated_grade.total_awarded == 4.0

    from lanes.p3_storage import P3Store
    audit_entries = P3Store(db_url).for_grade(grade_id)
    assert len(audit_entries) == 1
    assert audit_entries[0].reason == "Re-checked Q1 after student's regrade request"

    # --- Student sees the NEW score, the instructor's reply, and the
    #     resolved status -----------------------------------------------------
    at_maya2 = AppTest.from_file(APP_PATH)
    at_maya2.run()
    _login(at_maya2, "maya@uni.edu", "pw12345")
    assert "4/5" in at_maya2.metric[0].value
    assert any("resolved" in e.label.lower() for e in at_maya2.expander)
    _, messages = regrade_store.thread(request_id)
    assert [m.author_role for m in messages] == ["student", "instructor"]
    assert messages[1].body == "Good catch -- fixing it now."


def test_regrade_decline_path_closes_without_changing_the_grade(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from streamlit.testing.v1 import AppTest
    from lanes.p2_storage import P2Store

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="student", name="Ben", email="ben@uni.edu", password="pw12345")
    _login_as_instructor(at)
    _setup_course_assignment_and_grade(at, student_email="ben@uni.edu")

    grade_id = at.session_state["last_grade"][1].id
    _resolve_escalation_if_any(db_url, grade_id)
    original_award = P2Store(db_url).get_grade(grade_id).total_awarded

    at.sidebar.radio[0].set_value("Review & Feedback").run()
    assignment_choice = next(o for o in at.sidebar.selectbox[0].options if o != "-- choose --")
    at.sidebar.selectbox[0].select(assignment_choice).run()
    at.sidebar.selectbox[1].select(at.sidebar.selectbox[1].options[0]).run()
    next(b for b in at.sidebar.button if b.label == "Load this submission").click().run()
    next(b for b in at.button if b.label == "Publish grade").click().run()
    assert not at.error

    regrade_store = RegradeStore(db_url)
    from lanes.auth_storage import UserStore
    ben_id = UserStore(db_url).get_user_by_email("ben@uni.edu").id
    request = regrade_store.create_request(
        grade_id=str(grade_id), submission_id=str(at.session_state["last_grade"][0].id),
        assignment_id=str(at.session_state["assignment"].id), student_id=ben_id,
        body="Can you recheck this?",
    )

    at.run()  # pick up the request just created directly through the store
    request_box = next(sb for sb in at.selectbox if sb.label == "Requests (open first)")
    request_box.select(request_box.options[0]).run()
    at.text_area(key=f"regrade-queue-reply-{request.id}").set_value(
        "Reviewed it -- the score is correct as graded."
    ).run()
    next(b for b in at.button if b.label == "Send reply").click().run()
    next(b for b in at.button if b.label == "Close").click().run()

    assert not at.exception
    assert regrade_store.get_request(request.id).status == "closed"

    p2_store = P2Store(db_url)
    assert p2_store.get_grade(grade_id).total_awarded == original_award  # unchanged
    _, messages = regrade_store.thread(request.id)
    assert messages[-1].body == "Reviewed it -- the score is correct as graded."
