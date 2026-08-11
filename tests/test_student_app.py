"""AppTest walkthroughs for student_app.py -- the student portal (kept off
p3_app.py, which is instructor-only, per an explicit earlier decision).

Stage 4 turned this from Stage 3's single "pick one of your own graded
submissions" screen into the full portal: a home view of every course/
assignment the student is enrolled in (lanes/course_storage.py's
courses_for_student/assignments_for_course), each showing its status. These
tests seed a course with the assignment linked and the student enrolled --
not just an ownership record -- since that's now what makes a row appear
at all; a submission stamped for an assignment the student isn't (visibly)
enrolled in wouldn't show up in the home view, by design.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from contracts import ArtifactStatus
from lanes import p1_ingestion as p1
from lanes import p2_grading as p2
from lanes.auth_storage import User
from lanes.course_storage import CourseStore
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store

STUDENT_APP_PATH = str(Path(__file__).resolve().parent.parent / "student_app.py")
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _student_user(student_id: str = "student-1") -> User:
    return User(
        id=student_id, email=f"{student_id}@example.com", role="student",
        display_name="Stu", status="active", created_at=datetime.now(timezone.utc),
    )


def _seed_graded_submission(db_url: str, *, approved: bool, student_id: str = "student-1", course_name: str = "Course"):
    student_email = f"{student_id}@example.com"
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
        "Problem A\nWork: 2x+6=10\nFinal answer: x = 2", assignment=assignment, student_id=student_id,
    )
    context = p1.build_submission_context(assignment, submission, rubric)
    grade, trace = p2.grade(submission, rubric, context)
    if approved:
        grade.approver_id = "instructor_1"
        grade.status = ArtifactStatus.APPROVED
        # Stage 5: the student portal now keys visibility on `published`
        # (a softer, still-editable state), not the hard APPROVED lock --
        # finalize() sets both together, so a real "approved" grade sets
        # this too rather than leaving it stale here.
        grade.published = True

    p2_store = P2Store(db_url)
    p2_store.save(grade, trace)

    course_store = CourseStore(db_url)
    course = course_store.create_course("instructor-1", course_name)
    course_store.enroll_student(course.id, student_email)
    course_store.link_assignment_to_course(str(assignment.id), course.id)
    course_store.record_submission_owner(str(submission.id), student_id, str(assignment.id))
    return assignment, grade


def test_app_dispatcher_offers_the_student_portal_as_its_own_screen_and_only_that(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    at.radio[0].set_value("Register").run()
    at.selectbox(key="reg-role").select("student").run()
    at.text_input(key="reg-name").set_value("Stu Dent").run()
    at.text_input(key="reg-email").set_value("stu@example.com").run()
    at.text_input(key="reg-password").set_value("pw12345").run()
    at.button(key="register-submit").click().run()
    at.radio[0].set_value("Log in").run()
    at.text_input(key="login-email").set_value("stu@example.com").run()
    at.text_input(key="login-password").set_value("pw12345").run()
    at.button(key="login-submit").click().run()

    # Stage 3: a student's sidebar has exactly one page -- their own
    # portal, never an instructor or admin screen.
    assert list(at.sidebar.radio[0].options) == ["Student Feedback Chat"]


def test_student_app_shows_awaiting_grade_and_no_chat_before_approval(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'student.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _seed_graded_submission(db_url, approved=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.session_state["user"] = _student_user()
    at.run()

    assert len(at.chat_input) == 0
    assert any("awaiting grade" in c.value.lower() for c in at.caption)


def test_student_app_chat_is_grounded_and_remembers_the_conversation(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'student.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _seed_graded_submission(db_url, approved=True)

    from streamlit.testing.v1 import AppTest
    from lanes import p3_feedback

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.session_state["user"] = _student_user()
    at.run()

    assert any("graded" in c.value.lower() for c in at.caption)
    assert len(at.chat_input) == 1
    assert "5/5" in at.metric[0].value

    monkeypatch.setattr(p3_feedback, "call_model", lambda prompt, max_tokens=512: "You got full credit.")
    # timeout=30: asking a question now retrieves from the real textbook/
    # corpus (lanes/p1_rag.py), which cold-starts chromadb + onnxruntime the
    # first time it runs in a process -- the exact same cold-start cost that
    # made other AppTest calls need this earlier in this project, and
    # .p1_textbook_index/ is gitignored so CI always pays it fresh.
    at.chat_input[0].set_value("Why did I get full credit?").run(timeout=30)

    messages = [m.value for m in at.markdown]
    assert any("Why did I get full credit?" in m for m in messages)
    assert any("You got full credit." in m for m in messages)


def test_student_app_retrieves_textbook_grounding_per_question(tmp_path, monkeypatch):
    # Retrieval is per-question (not reusing whatever was retrieved for the
    # original rubric) -- a follow-up can legitimately need different
    # course material than the problem statement did.
    db_url = f"sqlite:///{tmp_path / 'student.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _seed_graded_submission(db_url, approved=True)

    from streamlit.testing.v1 import AppTest
    from lanes import p1_rag

    # AppTest re-executes student_app.py's source (including its `from
    # lanes.p1_rag import ...` statement) fresh on every .run() -- patching
    # student_app's own already-imported name wouldn't survive that
    # re-exec, but patching the source module's attribute does, since the
    # fresh import statement re-reads it at each exec.
    queries = []
    monkeypatch.setattr(p1_rag, "retrieve_method_from_textbook", lambda q: queries.append(q) or "a snippet")

    calls = {}

    def fake_call_model(prompt, max_tokens=512):
        calls["prompt"] = prompt
        return "An answer."

    from lanes import p3_feedback
    monkeypatch.setattr(p3_feedback, "call_model", fake_call_model)

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.session_state["user"] = _student_user()
    at.run()
    at.chat_input[0].set_value("Why does substitution work here?").run()

    assert queries == ["Why does substitution work here?"]
    assert "a snippet" in calls["prompt"]


def test_student_app_never_shows_another_students_course_or_grade(tmp_path, monkeypatch):
    # The privacy fix Stage 3/4 exist for: two students, two separate
    # courses/submissions in the same database -- student-1 must see
    # exactly their own course and grade, never student-2's.
    db_url = f"sqlite:///{tmp_path / 'student.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _seed_graded_submission(db_url, approved=True, student_id="student-1", course_name="Algebra")
    _seed_graded_submission(db_url, approved=True, student_id="student-2", course_name="Calculus")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.session_state["user"] = _student_user("student-1")
    at.run()

    assert not at.exception
    assert [h.value for h in at.header] == ["Algebra"]


def test_student_app_with_no_enrolled_courses_shows_an_empty_state_not_an_error(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'student.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.session_state["user"] = _student_user("nobody-yet")
    at.run()

    assert not at.exception
    assert any("not enrolled in any courses" in i.value.lower() for i in at.info)


def test_student_app_with_no_logged_in_user_fails_closed(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'student.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _seed_graded_submission(db_url, approved=True)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.run()

    assert not at.exception
    assert any("must be logged in" in e.value.lower() for e in at.error)
    assert len(at.header) == 0
