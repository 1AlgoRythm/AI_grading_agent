"""Courses, enrollment, and submission ownership -- Stage 2 of the auth
build (Stage 1: lanes/auth_storage.py's users/roles/approval).

Mirrors the existing store pattern exactly (P1Store/P2Store/P3Store/
UserStore's own isolated DeclarativeBase, create_all in __init__, the same
DATABASE_URL). Additive tables only.

Enrollment is by email, not by picking an existing user: an instructor
enrolls `maya@uni.edu` into a course whether or not Maya has registered
yet. `resolve_enrollment_ids` fills in the real `student_id` once a
matching user exists -- called opportunistically, safe to call any time.

Assignment-course linking is a small mapping table (AssignmentCourseRecord)
rather than a new column on lanes/p1_storage.py's AssignmentRecord, so this
stays fully additive and never touches that table or its existing
`session.merge(...)`-based save path.

Submissions aren't stored standalone anywhere today -- a Submission only
ever lives in memory for the life of one grading call; lanes/p2_storage.py
persists the resulting Grade (by submission_id), never the Submission
object itself. SubmissionOwnerRecord is the durable link a later stage
needs to answer "which grades belong to student X": written once, when a
submission with a known student_id is graded, alongside the existing
P2Store.save(grade, trace) call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from contracts import new_id, now

if TYPE_CHECKING:
    from contracts import Grade
    from lanes.auth_storage import UserStore
    from lanes.p2_storage import P2Store

__all__ = ["Course", "CourseStore", "Enrollment"]


@dataclass(frozen=True)
class Course:
    id: str
    instructor_id: str
    name: str
    created_at: datetime


@dataclass(frozen=True)
class Enrollment:
    id: str
    course_id: str
    student_email: str
    student_id: Optional[str]
    created_at: datetime


class Base(DeclarativeBase):
    pass


class CourseRecord(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instructor_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EnrollmentRecord(Base):
    __tablename__ = "enrollments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), index=True)
    student_email: Mapped[str] = mapped_column(String(255), index=True)
    student_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssignmentCourseRecord(Base):
    __tablename__ = "assignment_courses"

    assignment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    course_id: Mapped[str] = mapped_column(String(36), index=True)


class SubmissionOwnerRecord(Base):
    __tablename__ = "submission_owners"

    submission_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    student_id: Mapped[str] = mapped_column(String(36), index=True)
    assignment_id: Mapped[str] = mapped_column(String(36), index=True)


def _to_course(record: CourseRecord) -> Course:
    return Course(
        id=record.id, instructor_id=record.instructor_id, name=record.name, created_at=record.created_at,
    )


def _to_enrollment(record: EnrollmentRecord) -> Enrollment:
    return Enrollment(
        id=record.id, course_id=record.course_id, student_email=record.student_email,
        student_id=record.student_id, created_at=record.created_at,
    )


class CourseStore:
    """Repository for courses, enrollment, assignment-course links, and
    submission ownership -- same shape as P1Store/P2Store/P3Store/UserStore."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)

    # ---- courses -------------------------------------------------------- #

    def create_course(self, instructor_id: str, name: str) -> Course:
        if not instructor_id or not name.strip():
            raise ValueError("instructor_id and name are required")
        record = CourseRecord(id=str(new_id()), instructor_id=instructor_id, name=name.strip(), created_at=now())
        with Session(self.engine) as session:
            session.add(record)
            session.commit()
            return _to_course(record)

    def get_course(self, course_id: str) -> Optional[Course]:
        with Session(self.engine) as session:
            record = session.get(CourseRecord, course_id)
            return _to_course(record) if record is not None else None

    def list_courses(self, instructor_id: Optional[str] = None) -> list[Course]:
        query = select(CourseRecord)
        if instructor_id is not None:
            query = query.where(CourseRecord.instructor_id == instructor_id)
        query = query.order_by(CourseRecord.created_at)
        with Session(self.engine) as session:
            return [_to_course(record) for record in session.scalars(query).all()]

    # ---- enrollment ------------------------------------------------------ #

    def enroll_student(self, course_id: str, student_email: str) -> Enrollment:
        student_email = student_email.strip().lower()
        if not student_email:
            raise ValueError("student_email is required")
        if self.get_course(course_id) is None:
            raise KeyError(f"no course with id {course_id!r}")

        with Session(self.engine) as session:
            existing = session.scalars(
                select(EnrollmentRecord).where(
                    EnrollmentRecord.course_id == course_id,
                    EnrollmentRecord.student_email == student_email,
                )
            ).first()
            if existing is not None:
                # Idempotent: enrolling the same email into the same course
                # twice returns the existing enrollment rather than
                # silently duplicating the roster row.
                return _to_enrollment(existing)

            record = EnrollmentRecord(
                id=str(new_id()), course_id=course_id, student_email=student_email,
                student_id=None, created_at=now(),
            )
            session.add(record)
            session.commit()
            return _to_enrollment(record)

    def list_enrollments(self, course_id: str) -> list[Enrollment]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(EnrollmentRecord)
                .where(EnrollmentRecord.course_id == course_id)
                .order_by(EnrollmentRecord.created_at)
            ).all()
            return [_to_enrollment(record) for record in records]

    def courses_for_student(self, student_email: str) -> list[Course]:
        student_email = student_email.strip().lower()
        with Session(self.engine) as session:
            course_ids = session.scalars(
                select(EnrollmentRecord.course_id).where(EnrollmentRecord.student_email == student_email)
            ).all()
            if not course_ids:
                return []
            records = session.scalars(
                select(CourseRecord).where(CourseRecord.id.in_(course_ids)).order_by(CourseRecord.created_at)
            ).all()
            return [_to_course(record) for record in records]

    def resolve_enrollment_ids(self, user_store: "UserStore") -> int:
        """Fill in `student_id` on every enrollment whose email now matches
        a registered user. Safe and idempotent to call any time (e.g. on
        every render of the enrollment screen) -- only ever touches
        enrollments that are still unresolved."""
        resolved = 0
        with Session(self.engine) as session:
            unresolved = session.scalars(
                select(EnrollmentRecord).where(EnrollmentRecord.student_id.is_(None))
            ).all()
            for record in unresolved:
                user = user_store.get_user_by_email(record.student_email)
                if user is not None:
                    record.student_id = user.id
                    resolved += 1
            if resolved:
                session.commit()
        return resolved

    # ---- assignment <-> course linking ------------------------------------ #

    def link_assignment_to_course(self, assignment_id: str, course_id: str) -> None:
        if self.get_course(course_id) is None:
            raise KeyError(f"no course with id {course_id!r}")
        with Session(self.engine) as session:
            session.merge(AssignmentCourseRecord(assignment_id=assignment_id, course_id=course_id))
            session.commit()

    def assignments_for_course(self, course_id: str) -> list[str]:
        with Session(self.engine) as session:
            return list(session.scalars(
                select(AssignmentCourseRecord.assignment_id).where(AssignmentCourseRecord.course_id == course_id)
            ).all())

    def course_for_assignment(self, assignment_id: str) -> Optional[str]:
        with Session(self.engine) as session:
            record = session.get(AssignmentCourseRecord, assignment_id)
            return record.course_id if record is not None else None

    # ---- submission ownership ------------------------------------------- #

    def record_submission_owner(self, submission_id: str, student_id: str, assignment_id: str) -> None:
        with Session(self.engine) as session:
            session.merge(SubmissionOwnerRecord(
                submission_id=submission_id, student_id=student_id, assignment_id=assignment_id,
            ))
            session.commit()

    def owner_for_submission(self, submission_id: str) -> Optional[str]:
        with Session(self.engine) as session:
            record = session.get(SubmissionOwnerRecord, submission_id)
            return record.student_id if record is not None else None

    def submissions_for_student(self, student_id: str) -> list[str]:
        with Session(self.engine) as session:
            return list(session.scalars(
                select(SubmissionOwnerRecord.submission_id).where(SubmissionOwnerRecord.student_id == student_id)
            ).all())

    def grades_for_student(self, student_id: str, p2_store: "P2Store") -> list["Grade"]:
        """Stage 3 of the auth build: the student portal's own lookup --
        every graded submission this student owns, across every assignment.
        Composes this store's ownership records with P2Store's own grade
        lookup rather than duplicating grade storage here. Empty, never an
        error, when the student owns nothing yet."""
        grades = []
        for submission_id in self.submissions_for_student(student_id):
            grades.extend(p2_store.grades_for_submission(submission_id))
        return grades
