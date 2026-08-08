"""[P2] SQL persistence for grades, grade traces, and critic results.

Plan §8: "Database tables (owned by feature) ... P2: grades, grade_traces,
critic_results." P2 owns persisting its own outputs -- a `Grade` and its
`Trace` only ever lived as in-memory Pydantic objects for the life of one
process before this module existed. This mirrors `lanes/p3_storage.py`'s
shape (P3's tables), but is entirely self-contained: it only depends on the
shared `contracts.py` models, not on any P1/P3 lane module.

Full-fidelity round-tripping uses Pydantic's own JSON (de)serialization
(`model_dump_json` / `model_validate_json`) rather than hand-mapping every
field to a column, so `Grade`/`Trace` can evolve (contracts.py decision 7:
the `Step` list is deliberately open) without a matching migration here.
Indexed columns exist alongside the JSON blob purely so common lookups
(by submission, by grade) don't require deserializing every row.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from contracts import Grade, Trace

__all__ = ["P2Store", "GradeRecord", "GradeTraceRecord", "CriticResultRecord"]


class Base(DeclarativeBase):
    pass


class GradeRecord(Base):
    __tablename__ = "grades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    submission_id: Mapped[str] = mapped_column(String(36), index=True)
    assignment_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32))
    escalated: Mapped[bool] = mapped_column(Boolean)
    resolution: Mapped[str] = mapped_column(String(32))
    total_awarded: Mapped[float] = mapped_column(Float)
    total_possible: Mapped[float] = mapped_column(Float)
    payload_json: Mapped[str] = mapped_column(Text)


class GradeTraceRecord(Base):
    __tablename__ = "grade_traces"

    grade_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stop_reason: Mapped[str] = mapped_column(String(32))
    critic_agreement: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    num_revisions: Mapped[int] = mapped_column(Integer)
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)


class CriticResultRecord(Base):
    """One row per individual critic judgment, extracted from the trace's
    CRITIQUE steps -- lets the critic-agreement rate (§13) be queried
    directly instead of re-parsing every trace's step list."""

    __tablename__ = "critic_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    grade_id: Mapped[str] = mapped_column(String(36), index=True)
    problem_id: Mapped[str] = mapped_column(String(36))
    agrees: Mapped[bool] = mapped_column(Boolean)
    critique: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_revision: Mapped[bool] = mapped_column(Boolean)


class P2Store:
    """Repository for a grading run's outputs, used by `p2_app.py` today and
    swappable to a shared PostgreSQL instance in deployment (compose.yaml's
    `DATABASE_URL`)."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)

    def save(self, grade: Grade, trace: Trace) -> None:
        """Persist (or re-persist, e.g. after a re-score) a Grade + its Trace."""
        with Session(self.engine) as session:
            session.merge(GradeRecord(
                id=str(grade.id),
                submission_id=str(grade.submission_id),
                assignment_id=str(grade.assignment_id),
                status=grade.status.value,
                escalated=grade.escalated,
                resolution=grade.resolution.value,
                total_awarded=grade.total_awarded,
                total_possible=grade.total_possible,
                payload_json=grade.model_dump_json(),
            ))
            session.merge(GradeTraceRecord(
                grade_id=str(grade.id),
                stop_reason=trace.stop_reason.value,
                critic_agreement=trace.critic_agreement,
                num_revisions=trace.num_revisions,
                tokens_used=trace.tokens_used,
                latency_ms=trace.latency_ms,
                payload_json=trace.model_dump_json(),
            ))
            session.query(CriticResultRecord).filter_by(grade_id=str(grade.id)).delete()
            for step in trace.steps:
                if step.type != "critique":
                    continue
                session.add(CriticResultRecord(
                    grade_id=str(grade.id),
                    problem_id=str(step.data.get("problem_id") or step.data.get("problem") or ""),
                    agrees=bool(step.data.get("agrees")),
                    critique=step.data.get("critique"),
                    after_revision=bool(step.data.get("after_revision", False)),
                ))
            session.commit()

    def get_grade(self, grade_id: UUID) -> Optional[Grade]:
        with Session(self.engine) as session:
            record = session.get(GradeRecord, str(grade_id))
        return Grade.model_validate_json(record.payload_json) if record else None

    def get_trace(self, grade_id: UUID) -> Optional[Trace]:
        with Session(self.engine) as session:
            record = session.get(GradeTraceRecord, str(grade_id))
        return Trace.model_validate_json(record.payload_json) if record else None

    def grades_for_submission(self, submission_id: UUID) -> list[Grade]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(GradeRecord).where(GradeRecord.submission_id == str(submission_id))
            ).all()
        return [Grade.model_validate_json(record.payload_json) for record in records]

    def grades_for_assignment(self, assignment_id: UUID) -> list[Grade]:
        """List every graded submission for an assignment -- the lookup a
        reviewer needs when they only know the assignment, not a specific
        submission_id (e.g. P3's review app picking which submission to
        open)."""
        with Session(self.engine) as session:
            records = session.scalars(
                select(GradeRecord).where(GradeRecord.assignment_id == str(assignment_id))
            ).all()
        return [Grade.model_validate_json(record.payload_json) for record in records]

    def critic_results_for(self, grade_id: UUID) -> list[CriticResultRecord]:
        with Session(self.engine) as session:
            return list(session.scalars(
                select(CriticResultRecord).where(CriticResultRecord.grade_id == str(grade_id))
            ).all())
