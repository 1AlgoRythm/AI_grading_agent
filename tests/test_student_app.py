"""AppTest walkthroughs for student_app.py -- the separate student-facing
feedback chat screen (kept off p3_app.py, which is instructor-only, per an
explicit earlier decision).
"""
from __future__ import annotations

from pathlib import Path

from contracts import ArtifactStatus
from lanes import p1_ingestion as p1
from lanes import p2_grading as p2
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store

STUDENT_APP_PATH = str(Path(__file__).resolve().parent.parent / "student_app.py")
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _seed_graded_submission(db_url: str, *, approved: bool):
    p1_store = P1Store(db_url)
    assignment = p1.ingest_assignment("Problem A (5 points): Solve for x: 2x + 6 = 10.")
    for prob in assignment.problems:
        p1.develop_solution(prob)
        prob.solution_status = ArtifactStatus.APPROVED
    p1_store.save_assignment(assignment)

    rubric = p1.draft_rubric(assignment, {})
    rubric.status = ArtifactStatus.APPROVED
    p1_store.save_rubric(rubric)

    submission = p1.ingest_submission("Problem A\nWork: 2x+6=10\nFinal answer: x = 2", assignment=assignment)
    context = p1.build_submission_context(assignment, submission, rubric)
    grade, trace = p2.grade(submission, rubric, context)
    if approved:
        grade.approver_id = "instructor_1"
        grade.status = ArtifactStatus.APPROVED

    p2_store = P2Store(db_url)
    p2_store.save(grade, trace)
    return assignment, grade


def test_app_dispatcher_offers_the_student_chat_as_its_own_screen(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    assert "Student Feedback Chat" in at.sidebar.radio[0].options


def test_student_app_shows_no_chat_for_a_grade_not_yet_approved(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'student.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _seed_graded_submission(db_url, approved=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.run()
    at.selectbox[0].select(at.selectbox[0].options[1]).run()
    at.selectbox[1].select(at.selectbox[1].options[0]).run()

    assert len(at.chat_input) == 0
    assert any("hasn't been finalized" in i.value for i in at.info)


def test_student_app_chat_is_grounded_and_remembers_the_conversation(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'student.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    _seed_graded_submission(db_url, approved=True)

    from streamlit.testing.v1 import AppTest
    from lanes import p3_feedback

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.run()
    at.selectbox[0].select(at.selectbox[0].options[1]).run()
    at.selectbox[1].select(at.selectbox[1].options[0]).run()

    assert len(at.chat_input) == 1
    assert "5/5" in at.metric[0].value

    monkeypatch.setattr(p3_feedback, "call_model", lambda prompt, max_tokens=512: "You got full credit.")
    at.chat_input[0].set_value("Why did I get full credit?").run()

    messages = [m.value for m in at.markdown]
    assert any("Why did I get full credit?" in m for m in messages)
    assert any("You got full credit." in m for m in messages)
