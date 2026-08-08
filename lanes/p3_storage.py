"""[P3] SQL persistence for override audits and evaluation runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from lanes.p3_evaluation import EvaluationReport
from lanes.p3_review import OverrideAuditEntry


class Base(DeclarativeBase):
    pass


class OverrideAuditRecord(Base):
    __tablename__ = "override_audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    grade_id: Mapped[str] = mapped_column(String(36), index=True)
    problem_id: Mapped[str] = mapped_column(String(36))
    approver_id: Mapped[str] = mapped_column(String(255))
    previous_points: Mapped[float] = mapped_column(Float)
    new_points: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvaluationRunRecord(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    report_json: Mapped[str] = mapped_column(Text)


class P3Store:
    """Repository used by the UI today and PostgreSQL in deployment."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)

    def append(self, entry: OverrideAuditEntry) -> None:
        with Session(self.engine) as session:
            session.add(OverrideAuditRecord(
                id=str(entry.id), grade_id=str(entry.grade_id),
                problem_id=str(entry.problem_id), approver_id=entry.approver_id,
                previous_points=entry.previous_points, new_points=entry.new_points,
                reason=entry.reason, created_at=entry.created_at,
            ))
            session.commit()

    def for_grade(self, grade_id: UUID) -> list[OverrideAuditEntry]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(OverrideAuditRecord)
                .where(OverrideAuditRecord.grade_id == str(grade_id))
                .order_by(OverrideAuditRecord.created_at)
            ).all()
        return [
            OverrideAuditEntry(
                id=UUID(item.id), grade_id=UUID(item.grade_id),
                problem_id=UUID(item.problem_id), approver_id=item.approver_id,
                previous_points=item.previous_points, new_points=item.new_points,
                reason=item.reason, created_at=item.created_at,
            )
            for item in records
        ]

    def save_evaluation(self, report: EvaluationReport, created_at: datetime) -> int:
        with Session(self.engine) as session:
            record = EvaluationRunRecord(
                created_at=created_at, report_json=json.dumps(asdict(report), sort_keys=True)
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record.id

    def evaluation_runs(self) -> list[EvaluationReport]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(EvaluationRunRecord).order_by(EvaluationRunRecord.id)
            ).all()
        return [EvaluationReport(**json.loads(item.report_json)) for item in records]
