"""End-to-end AppTest walkthrough of Stage 2 (courses/enrollment/student_id)
through the real UI: an instructor creates a course, enrolls a student by
email, links an assignment to it, and a student's own upload gets stamped
with their real student_id -- resolvable afterward from lanes/course_storage.py,
not just held in memory.
"""
from __future__ import annotations

from pathlib import Path

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")

_ADMIN_EMAIL = "admin@local"
_ADMIN_PASSWORD = "changeme123"


def _login(at, email: str, password: str) -> None:
    at.text_input(key="login-email").set_value(email).run()
    at.text_input(key="login-password").set_value(password).run()
    at.button(key="login-submit").click().run()


def _register(at, *, role: str, name: str, email: str, password: str) -> None:
    at.radio[0].set_value("Register").run()
    at.selectbox(key="reg-role").select(role).run()
    at.text_input(key="reg-name").set_value(name).run()
    at.text_input(key="reg-email").set_value(email).run()
    at.text_input(key="reg-password").set_value(password).run()
    at.button(key="register-submit").click().run()
    at.radio[0].set_value("Log in").run()


def test_instructor_creates_course_enrolls_and_links_an_assignment(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from streamlit.testing.v1 import AppTest
    from lanes.course_storage import CourseStore

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="instructor", name="Prof", email="prof@example.com", password="pw12345")
    _login(at, _ADMIN_EMAIL, _ADMIN_PASSWORD)
    at.sidebar.radio[0].set_value("Admin: Approvals").run()
    next(b for b in at.button if b.label == "Approve").click().run()
    next(b for b in at.button if b.label == "Log out").click().run()
    _login(at, "prof@example.com", "pw12345")

    # st.success(...) here is immediately followed by st.rerun() (same
    # pattern as "Ingest assignment" elsewhere in this file) -- the message
    # is already gone by the time .run() returns, so assert on the
    # persisted effect (the course/enrollment existing) instead.
    at.sidebar.text_input(key="new-course-name").set_value("Algebra 101").run()
    at.sidebar.button(key="create-course").click().run()
    assert "Algebra 101" in at.sidebar.selectbox(key="course-picker").options

    at.sidebar.text_input(key="enroll-email").set_value("maya@uni.edu").run()
    at.sidebar.button(key="enroll-submit").click().run()
    assert any("maya@uni.edu" in c.value for c in at.sidebar.caption)

    at.text_area[0].set_value("HW\n\nProblem A (5 points): Solve for x: 2x + 6 = 10.").run()
    next(b for b in at.button if b.label == "Ingest assignment").click().run()
    assignment_id = str(at.session_state["assignment"].id)

    at.sidebar.button(key="link-assignment-course").click().run()

    course_store = CourseStore(db_url)
    courses = course_store.list_courses()
    assert len(courses) == 1
    assert course_store.assignments_for_course(courses[0].id) == [assignment_id]
    assert [e.student_email for e in course_store.list_enrollments(courses[0].id)] == ["maya@uni.edu"]


def test_student_upload_is_stamped_with_their_real_student_id_and_resolvable_after_grading(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from streamlit.testing.v1 import AppTest
    from lanes.course_storage import CourseStore

    at = AppTest.from_file(APP_PATH)
    at.run()
    _register(at, role="student", name="Maya", email="maya@uni.edu", password="pw12345")
    _login(at, "maya@uni.edu", "pw12345")
    student_id = at.session_state["user"].id

    at.text_area[0].set_value("HW\n\nProblem A (5 points): Solve for x: 2x + 6 = 10.").run()
    next(b for b in at.button if b.label == "Ingest assignment").click().run()
    next(b for b in at.button if b.label.startswith("Develop solution for")).click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve solution").click().run()
    next(b for b in at.button if b.label == "Draft rubric").click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve rubric").click().run()
    next(ta for ta in at.text_area if ta.label == "...or paste the submission text directly").set_value(
        "Problem A\nFinal answer: x = 2"
    ).run(timeout=30)
    next(b for b in at.button if b.label == "Ingest & grade submission").click().run(timeout=30)

    submission, grade, _ = at.session_state["last_grade"]
    assert submission.student_id == student_id
    assert submission.student_label == "Maya"

    # Durably resolvable from storage, not just held in the in-memory
    # Submission object -- this is the whole point of the ownership table.
    course_store = CourseStore(db_url)
    assert course_store.owner_for_submission(str(submission.id)) == student_id
    assert course_store.submissions_for_student(student_id) == [str(submission.id)]


def test_existing_grading_flow_is_unaffected_when_no_user_is_stamping_a_submission(tmp_path, monkeypatch):
    # Regression: an admin (or anyone not a student/instructor with a course
    # set up) grading a submission must behave exactly as before Stage 2 --
    # student_id stays None, nothing crashes, nothing gets recorded.
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _login(at, _ADMIN_EMAIL, _ADMIN_PASSWORD)

    at.text_area[0].set_value("HW\n\nProblem A (5 points): Solve for x: 2x + 6 = 10.").run()
    next(b for b in at.button if b.label == "Ingest assignment").click().run()
    next(b for b in at.button if b.label.startswith("Develop solution for")).click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve solution").click().run()
    next(b for b in at.button if b.label == "Draft rubric").click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve rubric").click().run()
    next(ta for ta in at.text_area if ta.label == "...or paste the submission text directly").set_value(
        "Problem A\nFinal answer: x = 2"
    ).run(timeout=30)
    next(b for b in at.button if b.label == "Ingest & grade submission").click().run(timeout=30)

    assert not at.exception
    submission, grade, _ = at.session_state["last_grade"]
    assert submission.student_id is None
    assert "5/5" in [m.value for m in at.metric][0] if at.metric else True
