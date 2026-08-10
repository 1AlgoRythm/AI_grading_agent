"""[P3] Human approval, score override, and audit logging."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from contracts import ArtifactStatus, Grade, GradeResolution, ProblemOutcome, now, round_to_step


@dataclass(frozen=True)
class OverrideAuditEntry:
    id: UUID
    grade_id: UUID
    problem_id: UUID
    approver_id: str
    previous_points: float
    new_points: float
    reason: str
    created_at: datetime


class AuditLog(Protocol):
    def append(self, entry: OverrideAuditEntry) -> None: ...


class InMemoryAuditLog:
    """Persistence adapter that can later be replaced by the planned database."""

    def __init__(self) -> None:
        self._entries: list[OverrideAuditEntry] = []

    def append(self, entry: OverrideAuditEntry) -> None:
        self._entries.append(entry)

    def for_grade(self, grade_id: UUID) -> list[OverrideAuditEntry]:
        return [item for item in self._entries if item.grade_id == grade_id]


def override_problem_score(
    grade: Grade,
    problem_id: UUID,
    points_awarded: float,
    approver_id: str,
    reason: str,
    audit_log: AuditLog,
) -> Grade:
    """Apply a justified human override and record before/after values."""
    approver_id, reason = approver_id.strip(), reason.strip()
    if not approver_id:
        raise ValueError("approver_id is required")
    if not reason:
        raise ValueError("an override reason is required")
    if grade.status is ArtifactStatus.APPROVED:
        raise ValueError("an approved grade cannot be overridden")

    problem_grade = next(
        (item for item in grade.problem_grades if item.problem_id == problem_id), None
    )
    if problem_grade is None:
        raise KeyError(f"problem {problem_id} is not present in grade {grade.id}")
    adjusted = round_to_step(points_awarded)
    if adjusted < 0 or adjusted > problem_grade.points_possible:
        raise ValueError("override score must be within the problem point range")

    previous = problem_grade.points_awarded
    # The human's decision has two homes: `partial_credit_reason` (below) and
    # the audit log entry (recording approver, previous/new points, reason).
    # `evidence` is the AI's original grading evidence and is deliberately
    # never touched here -- writing the override reason into it too was a
    # second copy of the same information with no clear owner, and repeated
    # overrides piled "Human override: ..." onto it forever, which leaked
    # straight into the feedback panel and the student chat (both read
    # `evidence` verbatim). The audit log is the single source of truth for
    # "who changed what, and why."
    #
    # ProblemGrade validates on every individual attribute assignment
    # (validate_assignment=True), so this order matters: a NO_ANSWER/
    # UNGRADEABLE entry has points_awarded=0 and no partial_credit_reason,
    # and flipping `outcome` to GRADED first, before points_awarded catches
    # up, would transiently look like unjustified partial credit and fail
    # validation right there. Set a placeholder reason before that flip so
    # the intermediate state is always valid.
    if problem_grade.points_awarded < problem_grade.points_possible:
        problem_grade.partial_credit_reason = reason
    problem_grade.outcome = ProblemOutcome.GRADED
    if adjusted < problem_grade.points_possible:
        problem_grade.partial_credit_reason = reason
        problem_grade.points_awarded = adjusted
    else:
        problem_grade.points_awarded = adjusted
        problem_grade.partial_credit_reason = None
    grade.resolution = GradeResolution.HUMAN_OVERRIDDEN
    grade.approver_id = approver_id
    # finalize() refuses an escalated grade -- and nothing ever cleared the
    # flag, so an escalated grade was a permanent dead end with no path to
    # approval. The human judgment the escalation was asking for has now
    # been made.
    grade.escalated = False
    audit_log.append(OverrideAuditEntry(
        id=uuid4(), grade_id=grade.id, problem_id=problem_id,
        approver_id=approver_id, previous_points=previous, new_points=adjusted,
        reason=reason, created_at=now(),
    ))
    return grade


def finalize(grade: Grade, approver_id: str) -> Grade:
    """Approve a reviewed grade while preserving override provenance."""
    approver_id = approver_id.strip()
    if not approver_id:
        raise ValueError("approver_id is required")
    if grade.status is ArtifactStatus.APPROVED:
        raise ValueError("grade is already approved")
    if grade.escalated:
        raise ValueError("an escalated grade must be resolved before approval")
    grade.approver_id = approver_id
    if grade.resolution is not GradeResolution.HUMAN_OVERRIDDEN:
        grade.resolution = GradeResolution.HUMAN_CONFIRMED
    grade.approved_at = now()
    grade.status = ArtifactStatus.APPROVED
    return grade
