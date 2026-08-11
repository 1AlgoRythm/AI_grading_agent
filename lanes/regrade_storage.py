"""Regrade requests -- Stage 4 of the auth build.

Minimal, additive: a student can open a request tied to one of their own
graded submissions, exchange messages with their instructor, and track its
status. Mirrors the existing store pattern (lanes/course_storage.py,
lanes/auth_storage.py) exactly -- its own isolated DeclarativeBase,
create_all in __init__, the same DATABASE_URL every other store uses.

This stage only builds and exercises the student side end to end. An
instructor-side queue (listing/resolving requests across all their
students) is a later/parallel stage; `requests_for_student` plus
`messages_for_request`/`thread` are the durable, additive surface that
stage will consume -- nothing here is student-only by construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from contracts import new_id, now

__all__ = ["RegradeMessage", "RegradeRequest", "RegradeStore", "STATUSES"]

STATUSES = ("open", "resolved", "closed")


@dataclass(frozen=True)
class RegradeRequest:
    id: str
    grade_id: str
    submission_id: str
    assignment_id: str
    student_id: str
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class RegradeMessage:
    id: str
    request_id: str
    author_role: str
    author_id: str
    body: str
    created_at: datetime


class Base(DeclarativeBase):
    pass


class RegradeRequestRecord(Base):
    __tablename__ = "regrade_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    grade_id: Mapped[str] = mapped_column(String(36), index=True)
    submission_id: Mapped[str] = mapped_column(String(36), index=True)
    assignment_id: Mapped[str] = mapped_column(String(36), index=True)
    student_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RegradeMessageRecord(Base):
    __tablename__ = "regrade_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    author_role: Mapped[str] = mapped_column(String(20))
    author_id: Mapped[str] = mapped_column(String(36))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _to_request(record: RegradeRequestRecord) -> RegradeRequest:
    return RegradeRequest(
        id=record.id, grade_id=record.grade_id, submission_id=record.submission_id,
        assignment_id=record.assignment_id, student_id=record.student_id, status=record.status,
        created_at=record.created_at, updated_at=record.updated_at,
    )


def _to_message(record: RegradeMessageRecord) -> RegradeMessage:
    return RegradeMessage(
        id=record.id, request_id=record.request_id, author_role=record.author_role,
        author_id=record.author_id, body=record.body, created_at=record.created_at,
    )


class RegradeStore:
    """Repository for regrade requests and their message threads -- same
    shape as P1Store/P2Store/P3Store/UserStore/CourseStore."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)

    def create_request(
        self, *, grade_id: str, submission_id: str, assignment_id: str, student_id: str, body: str,
    ) -> RegradeRequest:
        body = body.strip()
        if not body:
            raise ValueError("a regrade request needs an opening message")
        timestamp = now()
        record = RegradeRequestRecord(
            id=str(new_id()), grade_id=grade_id, submission_id=submission_id,
            assignment_id=assignment_id, student_id=student_id, status="open",
            created_at=timestamp, updated_at=timestamp,
        )
        with Session(self.engine) as session:
            session.add(record)
            session.add(RegradeMessageRecord(
                id=str(new_id()), request_id=record.id, author_role="student",
                author_id=student_id, body=body, created_at=timestamp,
            ))
            session.commit()
            return _to_request(record)

    def add_message(self, request_id: str, *, author_role: str, author_id: str, body: str) -> RegradeMessage:
        body = body.strip()
        if not body:
            raise ValueError("message body must not be blank")
        with Session(self.engine) as session:
            request = session.get(RegradeRequestRecord, request_id)
            if request is None:
                raise KeyError(f"no regrade request with id {request_id!r}")
            timestamp = now()
            message = RegradeMessageRecord(
                id=str(new_id()), request_id=request_id, author_role=author_role,
                author_id=author_id, body=body, created_at=timestamp,
            )
            session.add(message)
            request.updated_at = timestamp
            session.commit()
            return _to_message(message)

    def set_status(self, request_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
        with Session(self.engine) as session:
            request = session.get(RegradeRequestRecord, request_id)
            if request is None:
                raise KeyError(f"no regrade request with id {request_id!r}")
            request.status = status
            request.updated_at = now()
            session.commit()

    def get_request(self, request_id: str) -> Optional[RegradeRequest]:
        with Session(self.engine) as session:
            record = session.get(RegradeRequestRecord, request_id)
            return _to_request(record) if record is not None else None

    def messages_for_request(self, request_id: str) -> list[RegradeMessage]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(RegradeMessageRecord)
                .where(RegradeMessageRecord.request_id == request_id)
                .order_by(RegradeMessageRecord.created_at)
            ).all()
            return [_to_message(r) for r in records]

    def thread(self, request_id: str) -> Optional[tuple[RegradeRequest, list[RegradeMessage]]]:
        request = self.get_request(request_id)
        if request is None:
            return None
        return request, self.messages_for_request(request_id)

    def requests_for_student(self, student_id: str) -> list[RegradeRequest]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(RegradeRequestRecord)
                .where(RegradeRequestRecord.student_id == student_id)
                .order_by(RegradeRequestRecord.created_at)
            ).all()
            return [_to_request(r) for r in records]
