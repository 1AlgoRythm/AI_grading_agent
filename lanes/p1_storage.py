"""[P1] SQL persistence for assignments, problems, rubrics, and the
textbook index (plan §8: P1 owns these tables).

Mirrors `lanes/p3_storage.py`'s style: a thin SQLAlchemy repository the UI
reads/writes today, and Postgres uses in deployment. Whole child collections
(a rubric's criteria, an assignment's problems) are replaced wholesale on
each save — simplest-correct behavior for still-in-review drafts, where
partial-update races aren't a real concern.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from contracts import ArtifactStatus, Assignment, Problem, Rubric, RubricCriterion, SolutionSource, now


class Base(DeclarativeBase):
    pass


class AssignmentRecord(Base):
    __tablename__ = "assignments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    label: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    type: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ProblemRecord(Base):
    __tablename__ = "problems"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(String(36), index=True)
    label: Mapped[str] = mapped_column(String(255))
    statement: Mapped[str] = mapped_column(Text)
    points_possible: Mapped[float] = mapped_column(Float)
    reference_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reference_solution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    solution_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    solution_status: Mapped[str] = mapped_column(String(20))


class RubricRecord(Base):
    __tablename__ = "rubrics"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    leniency_note: Mapped[str] = mapped_column(Text)


class RubricCriterionRecord(Base):
    __tablename__ = "rubric_criteria"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rubric_id: Mapped[str] = mapped_column(String(36), index=True)
    problem_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    points: Mapped[float] = mapped_column(Float)
    failure_signals_json: Mapped[str] = mapped_column(Text)


class TextbookChunkRecord(Base):
    __tablename__ = "textbook_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_path: Mapped[str] = mapped_column(String(1000), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class P1Store:
    """Repository used by the P1 UI today and PostgreSQL in deployment."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, connect_args=connect_args)
        Base.metadata.create_all(self.engine)

    # ---- assignments + problems -------------------------------------- #

    def save_assignment(self, assignment: Assignment) -> None:
        with Session(self.engine) as session:
            session.merge(AssignmentRecord(
                id=str(assignment.id), label=assignment.label, title=assignment.title,
                type=assignment.type, created_at=now(),
            ))
            session.execute(delete(ProblemRecord).where(ProblemRecord.assignment_id == str(assignment.id)))
            for problem in assignment.problems:
                session.add(ProblemRecord(
                    id=str(problem.id), assignment_id=str(assignment.id), label=problem.label,
                    statement=problem.statement, points_possible=problem.points_possible,
                    reference_answer=problem.reference_answer, reference_solution=problem.reference_solution,
                    solution_source=problem.solution_source.value if problem.solution_source else None,
                    solution_status=problem.solution_status.value,
                ))
            session.commit()

    def load_assignment(self, assignment_id: UUID) -> Optional[Assignment]:
        with Session(self.engine) as session:
            record = session.get(AssignmentRecord, str(assignment_id))
            if record is None:
                return None
            problem_records = session.scalars(
                select(ProblemRecord).where(ProblemRecord.assignment_id == str(assignment_id))
            ).all()
            return self._assignment_from_records(record, problem_records)

    def list_assignments(self) -> list[Assignment]:
        with Session(self.engine) as session:
            records = session.scalars(select(AssignmentRecord)).all()
            assignments = []
            for record in records:
                problem_records = session.scalars(
                    select(ProblemRecord).where(ProblemRecord.assignment_id == record.id)
                ).all()
                assignments.append(self._assignment_from_records(record, problem_records))
            return assignments

    @staticmethod
    def _assignment_from_records(record: AssignmentRecord, problem_records) -> Assignment:
        return Assignment(
            id=UUID(record.id), label=record.label, title=record.title, type=record.type,
            problems=[
                Problem(
                    id=UUID(p.id), assignment_id=UUID(p.assignment_id), label=p.label,
                    statement=p.statement, points_possible=p.points_possible,
                    reference_answer=p.reference_answer, reference_solution=p.reference_solution,
                    solution_source=SolutionSource(p.solution_source) if p.solution_source else None,
                    solution_status=ArtifactStatus(p.solution_status),
                )
                for p in problem_records
            ],
        )

    # ---- rubrics + criteria -------------------------------------------- #

    def save_rubric(self, rubric: Rubric) -> None:
        with Session(self.engine) as session:
            session.merge(RubricRecord(
                id=str(rubric.id), assignment_id=str(rubric.assignment_id), version=rubric.version,
                status=rubric.status.value, leniency_note=rubric.leniency_note,
            ))
            session.execute(delete(RubricCriterionRecord).where(RubricCriterionRecord.rubric_id == str(rubric.id)))
            for criterion in rubric.criteria:
                session.add(RubricCriterionRecord(
                    id=str(criterion.id), rubric_id=str(rubric.id), problem_id=str(criterion.problem_id),
                    name=criterion.name, description=criterion.description, points=criterion.points,
                    failure_signals_json=json.dumps(criterion.failure_signals),
                ))
            session.commit()

    def load_rubric(self, rubric_id: UUID) -> Optional[Rubric]:
        with Session(self.engine) as session:
            record = session.get(RubricRecord, str(rubric_id))
            if record is None:
                return None
            criterion_records = session.scalars(
                select(RubricCriterionRecord).where(RubricCriterionRecord.rubric_id == str(rubric_id))
            ).all()
        return Rubric(
            id=UUID(record.id), assignment_id=UUID(record.assignment_id), version=record.version,
            status=ArtifactStatus(record.status), leniency_note=record.leniency_note,
            criteria=[
                RubricCriterion(
                    id=UUID(c.id), problem_id=UUID(c.problem_id), name=c.name, description=c.description,
                    points=c.points, failure_signals=json.loads(c.failure_signals_json),
                )
                for c in criterion_records
            ],
        )

    def load_rubric_for_assignment(self, assignment_id: UUID) -> Optional[Rubric]:
        """Look up a rubric by assignment rather than by its own id -- the
        lookup P3 actually has on hand (an assignment, not a rubric_id).
        Prefers the approved rubric; falls back to the latest version if
        none is approved yet."""
        with Session(self.engine) as session:
            record = session.scalars(
                select(RubricRecord)
                .where(
                    RubricRecord.assignment_id == str(assignment_id),
                    RubricRecord.status == ArtifactStatus.APPROVED.value,
                )
                .order_by(RubricRecord.version.desc())
            ).first()
            if record is None:
                record = session.scalars(
                    select(RubricRecord)
                    .where(RubricRecord.assignment_id == str(assignment_id))
                    .order_by(RubricRecord.version.desc())
                ).first()
        return self.load_rubric(UUID(record.id)) if record else None

    # ---- textbook index -------------------------------------------- #

    def index_textbook_chunks(self, source_path: str, chunks: list[str]) -> None:
        with Session(self.engine) as session:
            session.execute(delete(TextbookChunkRecord).where(TextbookChunkRecord.source_path == source_path))
            for index, chunk in enumerate(chunks, start=1):
                # Postgres text columns reject an embedded NUL (0x00) byte
                # outright -- sqlite (local dev) accepts it silently, which
                # is exactly how a stray NUL from a PDF-extraction artifact
                # sat unnoticed in the corpus until the first real-Postgres
                # deployment crashed indexing it. Stripped here defensively
                # (the actual DB write boundary) regardless of which source
                # file it came from, on top of the fix at PDF-extraction time
                # in lanes/p1_io.py's _read_pdf_text.
                session.add(TextbookChunkRecord(
                    source_path=source_path, chunk_index=index, content=chunk.replace("\x00", ""),
                ))
            session.commit()

    def textbook_chunks(self) -> list[tuple[str, str]]:
        with Session(self.engine) as session:
            records = session.scalars(
                select(TextbookChunkRecord)
                .order_by(TextbookChunkRecord.source_path, TextbookChunkRecord.chunk_index)
            ).all()
        return [(r.source_path, r.content) for r in records]
