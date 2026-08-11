"""Stage 4 of the auth build: the full student portal (enrolled courses/
assignments with status, self-upload, published grade + feedback + rubric,
regrade requests) plus two chatbot bug fixes discovered while working in
lanes/p3_feedback.py.

Reuses Stage 2/3's ownership plumbing (lanes/course_storage.py) and the
existing ingest_submission/build_submission_context/grade/generate_feedback/
answer_followup functions as-is -- this only adds lanes/regrade_storage.py
(a minimal, additive backend the student side alone exercises here; an
instructor-side queue is a later/parallel stage) and the two bug fixes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import fixtures as f
import pytest
from contracts import ArtifactStatus
from lanes import p1_ingestion as p1
from lanes import p2_grading as p2
from lanes import p3_feedback
from lanes.auth_storage import User
from lanes.course_storage import CourseStore
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store
from lanes.p3_feedback import (
    FeedbackContext,
    answer_followup,
    clear_feedback_contexts,
    feedback_history,
    register_feedback_context,
    _grounding_block,
)
from lanes.regrade_storage import RegradeStore

STUDENT_APP_PATH = str(Path(__file__).resolve().parent.parent / "student_app.py")
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _student_user(student_id: str = "student-1") -> User:
    return User(
        id=student_id, email=f"{student_id}@example.com", role="student",
        display_name="Stu", status="active", created_at=datetime.now(timezone.utc),
    )


def _seed_enrolled_assignment(db_url: str, *, student_id: str, course_name: str = "Course"):
    """A course with the assignment linked and this student enrolled, and
    an approved rubric -- the minimum the home view and self-upload need."""
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

    course_store = CourseStore(db_url)
    course = course_store.create_course("instructor-1", course_name)
    course_store.enroll_student(course.id, student_email)
    course_store.link_assignment_to_course(str(assignment.id), course.id)
    return assignment


# ---- Step 1/3: a student sees only their own courses/assignments/grades - #

def test_a_student_sees_only_their_own_enrolled_courses_and_grades(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    _seed_enrolled_assignment(db_url, student_id="student-a", course_name="Algebra")
    _seed_enrolled_assignment(db_url, student_id="student-b", course_name="Calculus")

    course_store = CourseStore(db_url)
    a_courses = {c.name for c in course_store.courses_for_student("student-a@example.com")}
    b_courses = {c.name for c in course_store.courses_for_student("student-b@example.com")}

    assert a_courses == {"Algebra"}
    assert b_courses == {"Calculus"}


# ---- Step 2: self-upload stamps the correct student_id -------------------- #

def test_student_self_upload_through_the_real_portal_stamps_their_own_student_id(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    assignment = _seed_enrolled_assignment(db_url, student_id="student-1")

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.session_state["user"] = _student_user("student-1")
    at.run()

    assert any("not submitted" in c.value.lower() for c in at.caption)

    at.text_area(key=f"upload-text-{assignment.id}").set_value(
        "Problem A\nWork: 2x+6=10\nFinal answer: x = 2"
    ).run()
    next(b for b in at.button if b.label == "Submit").click().run(timeout=30)

    assert not at.exception
    assert any("submitted" in s.value.lower() for s in at.success)

    course_store = CourseStore(db_url)
    submission_ids = course_store.submissions_for_student("student-1")
    assert len(submission_ids) == 1
    assert course_store.owner_for_submission(submission_ids[0]) == "student-1"


# ---- Step 4: regrade requests -- student side, minimal backend ----------- #

def test_regrade_request_is_created_and_retrievable_only_for_the_owning_student(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    store = RegradeStore(db_url)

    request = store.create_request(
        grade_id="grade-1", submission_id="sub-1", assignment_id="assign-1",
        student_id="student-a", body="I think Q1 deserves full credit.",
    )

    assert request.status == "open"
    assert [r.id for r in store.requests_for_student("student-a")] == [request.id]
    assert store.requests_for_student("student-b") == []  # never another student's

    thread = store.thread(request.id)
    assert thread is not None
    req, messages = thread
    assert req.id == request.id
    assert [m.body for m in messages] == ["I think Q1 deserves full credit."]

    store.add_message(request.id, author_role="instructor", author_id="prof-1", body="Looking into it.")
    _, messages = store.thread(request.id)
    assert [m.author_role for m in messages] == ["student", "instructor"]


def test_regrade_request_requires_a_non_blank_opening_message(tmp_path):
    store = RegradeStore(f"sqlite:///{tmp_path / 'app.db'}")
    with pytest.raises(ValueError):
        store.create_request(
            grade_id="g", submission_id="s", assignment_id="a", student_id="student-a", body="   ",
        )


def test_regrade_request_via_the_real_portal_ui_is_scoped_to_the_logged_in_student(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'app.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    assignment = _seed_enrolled_assignment(db_url, student_id="student-1")

    p1_store = P1Store(db_url)
    rubric = p1_store.load_rubric_for_assignment(assignment.id)
    submission = p1.ingest_submission(
        "Problem A\nFinal answer: x = 2", assignment=assignment, student_id="student-1",
    )
    context = p1.build_submission_context(assignment, submission, rubric)
    grade, trace = p2.grade(submission, rubric, context)
    grade.approver_id = "instructor_1"
    grade.status = ArtifactStatus.APPROVED
    P2Store(db_url).save(grade, trace)
    CourseStore(db_url).record_submission_owner(str(submission.id), "student-1", str(assignment.id))

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(STUDENT_APP_PATH)
    at.session_state["user"] = _student_user("student-1")
    at.run()

    at.text_area(key=f"regrade-new-{grade.id}").set_value("Please recheck Q1.").run()
    next(b for b in at.button if b.label == "Send regrade request").click().run()

    assert not at.exception
    regrade_store = RegradeStore(db_url)
    requests = regrade_store.requests_for_student("student-1")
    assert len(requests) == 1
    assert requests[0].grade_id == str(grade.id)
    _, messages = regrade_store.thread(requests[0].id)
    assert messages[0].body == "Please recheck Q1."
    assert regrade_store.requests_for_student("someone-else") == []


# ---- Step 5: chatbot bug fixes -------------------------------------------- #

def test_bug2_offline_stub_answer_is_not_presented_as_a_genuine_grounded_answer(monkeypatch):
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    clear_feedback_contexts()
    register_feedback_context(f.sample_grade(), f.sample_rubric())

    answer = answer_followup("Why did I lose points on Q2?", f.SID)

    assert "no grading model is configured" in answer.lower()
    # Still recorded -- as the honest message, not silently dropped -- so
    # the student sees *something* rather than a turn that went nowhere.
    assert feedback_history(f.SID) == [("Why did I lose points on Q2?", answer)]


def test_bug2_a_monkeypatched_real_looking_answer_is_still_trusted_verbatim(monkeypatch):
    # Regression guard for the fix above: offline tests (and any caller
    # that swaps call_model to simulate a working model) must not have
    # their genuine answers misdetected as the stub just because env vars
    # happen to be unset in the test environment.
    clear_feedback_contexts()
    register_feedback_context(f.sample_grade(), f.sample_rubric())
    monkeypatch.setattr(p3_feedback, "call_model", lambda prompt, max_tokens=512: "You got full credit.")

    answer = answer_followup("Why?", f.SID)

    assert answer == "You got full credit."


def test_bug3_grounding_block_does_not_raise_on_an_assignment_rubric_mismatch():
    grade = f.sample_grade()
    mismatched_rubric = f.sample_rubric().model_copy(update={"assignment_id": uuid4()})
    context = FeedbackContext(grade=grade, rubric=mismatched_rubric, assignment=None, labels={})

    text = _grounding_block(context)  # must not raise

    assert "Grade recorded:" in text


def test_bug3_answer_followup_does_not_crash_on_a_corrupted_mismatched_context(monkeypatch):
    # register_feedback_context itself already refuses to register a
    # mismatched grade/rubric pair -- this pokes the module's own store
    # directly to reach the inconsistent-context state Bug #3 guards
    # against (e.g. a future caller that bypasses that validation).
    clear_feedback_contexts()
    grade = f.sample_grade()
    mismatched_rubric = f.sample_rubric().model_copy(update={"assignment_id": uuid4()})
    p3_feedback._FEEDBACK_CONTEXTS[f.SID] = FeedbackContext(
        grade=grade, rubric=mismatched_rubric, assignment=None, labels={},
    )
    monkeypatch.setattr(p3_feedback, "call_model", lambda prompt, max_tokens=512: "An answer anyway.")

    answer = answer_followup("Why?", f.SID)  # must not raise

    assert answer == "An answer anyway."
