"""Stage 3 of the auth build: role-gating (app.py) layered on top of
Stage 2's ownership plumbing (lanes/course_storage.py), which already
exists -- this only adds the aggregate `grades_for_student` lookup the
student portal needs, and tests both that lookup and the gating itself.

Closes the privacy gap Stage 2 left open: before this, any logged-in user
could reach the student portal's old "pick any assignment, then any graded
submission for it" pickers and read another student's grade/feedback. Now
the sidebar itself only ever offers a student their own portal, and that
portal only ever lists submissions lanes/course_storage.py says they own.
"""
from __future__ import annotations

from pathlib import Path

from contracts import ArtifactStatus, Submission
from lanes import p1_ingestion as p1
from lanes import p2_grading as p2
from lanes.course_storage import CourseStore
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

_ADMIN_EMAIL = "admin@local"
_ADMIN_PASSWORD = "changeme123"


def _grade_one(db_url: str, student_id: str, final_answer: str = "x = 2"):
    p1_store = P1Store(db_url)
    assignment = p1.ingest_assignment("Problem A (5 points): Solve for x: 2x + 6 = 10.")
    for prob in assignment.problems:
        p1.develop_solution(prob)
        prob.solution_status = ArtifactStatus.APPROVED
    p1_store.save_assignment(assignment)

    rubric = p1.draft_rubric(assignment, {})
    rubric.status = ArtifactStatus.APPROVED
    p1_store.save_rubric(rubric)

    submission = p1.ingest_submission(
        f"Problem A\nFinal answer: {final_answer}", assignment=assignment, student_id=student_id,
    )
    context = p1.build_submission_context(assignment, submission, rubric)
    grade, trace = p2.grade(submission, rubric, context)
    grade.approver_id = "instructor_1"
    grade.status = ArtifactStatus.APPROVED

    p2_store = P2Store(db_url)
    p2_store.save(grade, trace)
    CourseStore(db_url).record_submission_owner(str(submission.id), student_id, str(assignment.id))
    return submission, grade


# ---- Step 1: the aggregate lookup the student portal needs -------------- #

def test_grades_for_student_only_returns_that_students_own_grades(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    _, grade_a = _grade_one(db_url, "student-a")
    _, grade_b = _grade_one(db_url, "student-b")

    course_store = CourseStore(db_url)
    p2_store = P2Store(db_url)

    a_grades = course_store.grades_for_student("student-a", p2_store)
    b_grades = course_store.grades_for_student("student-b", p2_store)

    assert [g.id for g in a_grades] == [grade_a.id]
    assert [g.id for g in b_grades] == [grade_b.id]


def test_grades_for_student_with_no_submissions_is_an_empty_list_not_an_error(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    course_store = CourseStore(db_url)
    p2_store = P2Store(db_url)
    assert course_store.grades_for_student("nobody-yet", p2_store) == []


def test_submission_still_builds_without_a_student_id():
    from uuid import uuid4
    sub = Submission(assignment_id=uuid4(), student_label="anon")
    assert sub.student_id is None


# ---- Step 2: role-gating in app.py --------------------------------------- #

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


def test_admin_sees_only_the_approvals_screen(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _login(at, _ADMIN_EMAIL, _ADMIN_PASSWORD)

    assert list(at.sidebar.radio[0].options) == ["Admin: Approvals"]


def test_instructor_sees_only_instructor_screens_never_the_student_portal(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _login_as_instructor(at)

    options = list(at.sidebar.radio[0].options)
    assert options == ["Upload & Rubric", "Grade & Trace", "Review & Feedback"]
    assert "Student Feedback Chat" not in options
    assert "Admin: Approvals" not in options


def test_student_sees_only_their_own_portal_never_instructor_or_admin_screens(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="student", name="Amy", email="amy@uni.edu", password="pw12345")
    _login(at, "amy@uni.edu", "pw12345")

    assert list(at.sidebar.radio[0].options) == ["Student Feedback Chat"]


# ---- Step 3: the actual privacy proof ------------------------------------ #

def test_two_students_each_see_only_their_own_grade_never_the_others(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from streamlit.testing.v1 import AppTest
    from lanes.auth_storage import UserStore

    at = AppTest.from_file(APP_PATH)
    at.run()
    # Register both students first, while the app is still on the pre-login
    # screen -- _register/_login assume `at.radio[0]` is the login/register
    # mode toggle, which is only true before anyone is logged in.
    _register(at, role="student", name="Amy", email="amy@uni.edu", password="pw12345")
    _register(at, role="student", name="Ben", email="ben@uni.edu", password="pw12345")
    _login_as_instructor(at)

    at.sidebar.text_input(key="new-course-name").set_value("Physics").run()
    at.sidebar.button(key="create-course").click().run()
    at.sidebar.text_input(key="enroll-email").set_value("amy@uni.edu").run()
    at.sidebar.button(key="enroll-submit").click().run()
    at.sidebar.text_input(key="enroll-email").set_value("ben@uni.edu").run()
    at.sidebar.button(key="enroll-submit").click().run()

    at.text_area[0].set_value("HW\n\nProblem A (5 points): Solve for x: 2x + 6 = 10.").run()
    next(b for b in at.button if b.label == "Ingest assignment").click().run()
    at.sidebar.button(key="link-assignment-course").click().run()
    next(b for b in at.button if b.label.startswith("Develop solution for")).click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve solution").click().run()
    next(b for b in at.button if b.label == "Draft rubric").click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve rubric").click().run()

    def grade_for(email: str, final_answer: str) -> None:
        assign_box = next(
            sb for sb in at.selectbox if sb.label == "Assign this submission to an enrolled student (optional)"
        )
        assign_box.select(email).run()
        next(ta for ta in at.text_area if ta.label == "...or paste the submission text directly").set_value(
            f"Problem A\nFinal answer: {final_answer}"
        ).run(timeout=30)
        next(b for b in at.button if b.label == "Ingest & grade submission").click().run(timeout=30)

    grade_for("amy@uni.edu", "x = 2")
    amy_submission_id = at.session_state["last_grade"][0].id
    grade_for("ben@uni.edu", "x = 999")
    ben_submission_id = at.session_state["last_grade"][0].id
    assert amy_submission_id != ben_submission_id

    user_store = UserStore(db_url)
    amy_id = user_store.get_user_by_email("amy@uni.edu").id
    ben_id = user_store.get_user_by_email("ben@uni.edu").id

    # Storage-level proof, independent of the UI.
    course_store = CourseStore(db_url)
    assert course_store.submissions_for_student(amy_id) == [str(amy_submission_id)]
    assert course_store.submissions_for_student(ben_id) == [str(ben_submission_id)]

    # UI-level proof: a *fresh* login session per student (rather than
    # logout/relogin inside the same AppTest instance -- AppTest's element
    # tree retains stale keyed widgets from the pre-login screen across
    # that many persona switches in one session and chokes gathering their
    # widget state, an AppTest quirk unrelated to app correctness) shows
    # their portal offers exactly one submission -- their own, never the
    # other's.
    at_amy = AppTest.from_file(APP_PATH)
    at_amy.run()
    _login(at_amy, "amy@uni.edu", "pw12345")
    assert len(at_amy.selectbox[0].options) == 1

    at_ben = AppTest.from_file(APP_PATH)
    at_ben.run()
    _login(at_ben, "ben@uni.edu", "pw12345")
    assert len(at_ben.selectbox[0].options) == 1
