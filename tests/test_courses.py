"""Tests for lanes/course_storage.py -- courses, enrollment-by-email,
assignment<->course linking, and submission ownership. Stage 2 of the
multi-stage auth build (Stage 1: lanes/auth_storage.py's users/roles).
"""
from __future__ import annotations

import pytest

from contracts import Submission
from lanes.auth_storage import UserStore
from lanes.course_storage import CourseStore


def test_create_course_and_list_courses_by_instructor(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")

    course = store.create_course("instructor-1", "Intro to Algebra")

    assert course.instructor_id == "instructor-1"
    assert course.name == "Intro to Algebra"
    assert [c.id for c in store.list_courses(instructor_id="instructor-1")] == [course.id]
    assert store.list_courses(instructor_id="someone-else") == []
    assert store.get_course(course.id).name == "Intro to Algebra"


def test_create_course_requires_instructor_id_and_name(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    with pytest.raises(ValueError):
        store.create_course("", "A Course")
    with pytest.raises(ValueError):
        store.create_course("instructor-1", "   ")


def test_get_course_returns_none_for_an_unknown_id(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    assert store.get_course("not-a-real-id") is None


def test_enroll_student_by_email_and_list_enrollments(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    course = store.create_course("instructor-1", "Algebra")

    enrollment = store.enroll_student(course.id, "Maya@Uni.edu")

    assert enrollment.student_email == "maya@uni.edu"  # normalized to lowercase
    assert enrollment.student_id is None  # not registered yet
    assert [e.student_email for e in store.list_enrollments(course.id)] == ["maya@uni.edu"]


def test_enroll_student_is_idempotent_for_the_same_email_and_course(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    course = store.create_course("instructor-1", "Algebra")

    first = store.enroll_student(course.id, "maya@uni.edu")
    second = store.enroll_student(course.id, "maya@uni.edu")

    assert first.id == second.id
    assert len(store.list_enrollments(course.id)) == 1


def test_enroll_student_raises_for_an_unknown_course(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    with pytest.raises(KeyError):
        store.enroll_student("not-a-real-course", "maya@uni.edu")


def test_courses_for_student_returns_every_course_that_email_is_enrolled_in(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    algebra = store.create_course("instructor-1", "Algebra")
    calculus = store.create_course("instructor-1", "Calculus")
    store.create_course("instructor-1", "Unrelated Course")  # maya isn't enrolled here
    store.enroll_student(algebra.id, "maya@uni.edu")
    store.enroll_student(calculus.id, "maya@uni.edu")

    courses = store.courses_for_student("MAYA@uni.edu")  # lookup is also case-insensitive

    assert {c.name for c in courses} == {"Algebra", "Calculus"}


def test_resolve_enrollment_ids_fills_student_id_once_a_matching_user_exists(tmp_path):
    course_store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    user_store = UserStore(f"sqlite:///{tmp_path / 'auth.db'}")
    course = course_store.create_course("instructor-1", "Algebra")
    enrollment = course_store.enroll_student(course.id, "maya@uni.edu")
    assert enrollment.student_id is None

    # No matching user yet -- resolves nothing, never raises.
    resolved = course_store.resolve_enrollment_ids(user_store)
    assert resolved == 0
    assert course_store.list_enrollments(course.id)[0].student_id is None

    maya = user_store.create_user("maya@uni.edu", "pw", "student", "Maya", "active")
    resolved = course_store.resolve_enrollment_ids(user_store)

    assert resolved == 1
    assert course_store.list_enrollments(course.id)[0].student_id == maya.id

    # Idempotent: calling again with nothing left unresolved is a no-op.
    assert course_store.resolve_enrollment_ids(user_store) == 0


def test_link_assignment_to_course_and_query_both_directions(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    course = store.create_course("instructor-1", "Algebra")

    assert store.course_for_assignment("assignment-1") is None
    assert store.assignments_for_course(course.id) == []

    store.link_assignment_to_course("assignment-1", course.id)

    assert store.course_for_assignment("assignment-1") == course.id
    assert store.assignments_for_course(course.id) == ["assignment-1"]


def test_link_assignment_to_course_raises_for_an_unknown_course(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")
    with pytest.raises(KeyError):
        store.link_assignment_to_course("assignment-1", "not-a-real-course")


def test_record_and_resolve_submission_ownership(tmp_path):
    store = CourseStore(f"sqlite:///{tmp_path / 'courses.db'}")

    assert store.owner_for_submission("submission-1") is None
    assert store.submissions_for_student("student-1") == []

    store.record_submission_owner("submission-1", "student-1", "assignment-1")

    assert store.owner_for_submission("submission-1") == "student-1"
    assert store.submissions_for_student("student-1") == ["submission-1"]

    # Re-recording (e.g. re-grading the same submission) overwrites cleanly,
    # not a duplicate row.
    store.record_submission_owner("submission-1", "student-2", "assignment-1")
    assert store.owner_for_submission("submission-1") == "student-2"
    assert store.submissions_for_student("student-1") == []
    assert store.submissions_for_student("student-2") == ["submission-1"]


def test_submission_builds_with_and_without_student_id():
    # Backward compat: student_id is optional/defaulted -- every existing
    # caller that builds a Submission without it must keep working.
    from uuid import uuid4

    without = Submission(assignment_id=uuid4(), student_label="anon")
    assert without.student_id is None

    with_id = Submission(assignment_id=uuid4(), student_label="Maya", student_id="user-123")
    assert with_id.student_id == "user-123"
