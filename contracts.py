"""
AI Assignment Grading Agent — shared contracts.

This is the ONE frozen module all three lanes import and build against. Freeze
the shared objects and the function signatures; keep everything internal to a
single lane soft.

The eight decisions this encodes (see the project plan for the full rationale):

  1. IDs        — UUID identity + a mutable human `label`; references carry
                  parent UUIDs. Rename-safe; logs stay readable via `label`.
  2. Granularity— the grade() call takes the whole Submission; scoring and
                  critique happen per-problem inside; Grade holds per-problem
                  entries. Keeps math's independence while allowing future
                  types that need whole-submission context.
  3. Scoring    — validated float points_awarded/points_possible, rounded to a
                  configurable step; a reason is required on partial credit;
                  any letter/percentage mapping is centralized and deferred.
  4. Status     — a minimal gate enum (proposed|approved) answers "usable yet?";
                  `escalated` is a flag; provenance (ai_only / human_confirmed /
                  human_overridden + approver) lives separately for the audit log.
  5. Enums      — hard Enums for closed sets; an extensible registry for the
                  open assignment `type`; LLM-produced values get coerced.
  6. Context    — build_context returns a structured GradingContext that P1 has
                  already CURATED and BUDGET-CHECKED. P1 = select + budget;
                  P2 = frame the grader/critic prompts from the curated pieces.
  7. Trace      — freeze only the minimal envelope P3's eval consumes; keep the
                  step list open (Step{type, data}) so P2 can evolve internals.
  8. Errors     — domain outcomes (no answer / ungradeable / escalated) are DATA
                  in Grade, not errors. Real failures raise typed exceptions,
                  handled once at the batch boundary with per-submission isolation.

Ownership tags on the function stubs: [P1] ingestion/context/rubric,
[P2] grading agent + critic + tool, [P3] feedback/review/eval.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

# Decision 3: partial credit is rounded to this step so sums of partial credits
# don't drift (e.g. 4.9999). Configurable in one place, never in the UI.
DEFAULT_ROUNDING_STEP: float = 0.5

# Decision 6: P1 guarantees an assembled GradingContext fits this many tokens.
DEFAULT_TOKEN_BUDGET: int = 6000


def new_id() -> UUID:
    """Surrogate identity for every object (decision 1)."""
    return uuid4()


def now() -> datetime:
    return datetime.now(timezone.utc)


def round_to_step(value: float, step: float = DEFAULT_ROUNDING_STEP) -> float:
    """Round a score to the nearest allowed increment (decision 3).

    Rounds halves UP, in the student's favor, rather than using Python's default
    banker's rounding (which would send an exact 2.25 down to 2.0). Leniency is a
    stated grading principle, so an exact half goes to the student.
    """
    if step <= 0:
        return value
    return round(math.floor(value / step + 0.5) * step, 4)


def rough_token_estimate(text: str) -> int:
    """Cheap token estimate (~4 chars/token) for P1's budget check. Swap for a
    real tokenizer later; the seam does not change."""
    return max(0, len(text) // 4)


# --------------------------------------------------------------------------- #
# Enums — closed sets only (decision 5)
# --------------------------------------------------------------------------- #

class ArtifactStatus(str, Enum):
    """The gate on the three human-approved artifacts: solution, rubric, grade
    (decision 4). Answers one question — is this usable yet?"""
    PROPOSED = "proposed"
    APPROVED = "approved"


class SolutionSource(str, Enum):
    SAMPLE = "sample"        # a human-provided reference was ingested
    GENERATED = "generated"  # the system developed the solution


class GradeResolution(str, Enum):
    """Provenance for the audit log, kept separate from the usability gate
    (decision 4)."""
    AI_ONLY = "ai_only"                    # approved without human change
    HUMAN_CONFIRMED = "human_confirmed"    # human looked and agreed
    HUMAN_OVERRIDDEN = "human_overridden"  # human changed the score


class ProblemOutcome(str, Enum):
    """Domain outcomes are DATA, not errors (decision 8). All are valid grades."""
    GRADED = "graded"
    NO_ANSWER = "no_answer"        # student left it blank
    UNGRADEABLE = "ungradeable"    # present but not gradeable (e.g. unparseable)


class StopReason(str, Enum):
    COMPLETED = "completed"
    BUDGET_EXCEEDED = "budget_exceeded"
    ESCALATED = "escalated"
    ERROR = "error"


class StepKind:
    """Suggested step types for a Trace. NOT enforced — the Trace step list is
    deliberately open (decision 7) so P2 can evolve it. These are just the
    conventional strings so all three lanes spell them the same way."""
    REASON = "reason"
    ACT = "act"
    OBSERVE = "observe"
    CRITIQUE = "critique"
    REVISION = "revision"


# --------------------------------------------------------------------------- #
# Assignment type registry — the one OPEN set (decision 5)
# --------------------------------------------------------------------------- #

_ASSIGNMENT_TYPES: set[str] = {"math", "short_answer", "proof"}


def register_assignment_type(name: str) -> None:
    """Add a new assignment type (e.g. 'code', 'short_answer') without editing
    a frozen enum. Adding a type is how the system grows beyond math."""
    _ASSIGNMENT_TYPES.add(name.strip().lower())


def is_valid_assignment_type(name: str) -> bool:
    return name.strip().lower() in _ASSIGNMENT_TYPES


def known_assignment_types() -> set[str]:
    return set(_ASSIGNMENT_TYPES)


# --------------------------------------------------------------------------- #
# Core objects
# --------------------------------------------------------------------------- #

_MODEL_CONFIG = ConfigDict(extra="forbid", validate_assignment=True)


class Problem(BaseModel):
    """One problem in an assignment. Carries its own reference solution/answer,
    which is a human-gated artifact (solution_status)."""
    model_config = _MODEL_CONFIG

    id: UUID = Field(default_factory=new_id)
    assignment_id: UUID
    label: str                       # human-facing, e.g. "Q2" (mutable)
    statement: str
    points_possible: float = Field(ge=0)

    reference_answer: Optional[str] = None
    reference_solution: Optional[str] = None
    solution_source: Optional[SolutionSource] = None
    solution_status: ArtifactStatus = ArtifactStatus.PROPOSED


class Assignment(BaseModel):
    model_config = _MODEL_CONFIG

    id: UUID = Field(default_factory=new_id)
    label: str                       # e.g. "hw3"
    title: str
    type: str                        # validated against the registry
    problems: list[Problem] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        v = v.strip().lower()
        if not is_valid_assignment_type(v):
            raise ValueError(
                f"unknown assignment type {v!r}; register it with "
                f"register_assignment_type() first. Known: {sorted(_ASSIGNMENT_TYPES)}"
            )
        return v


class RubricCriterion(BaseModel):
    model_config = _MODEL_CONFIG

    id: UUID = Field(default_factory=new_id)
    problem_id: UUID                 # a criterion belongs to one problem
    name: str
    description: str
    points: float = Field(ge=0)
    failure_signals: list[str] = Field(default_factory=list)


class Rubric(BaseModel):
    """Per-problem criteria live in one list, each tagged with problem_id.
    Human-gated via status; versioned so edits are tracked."""
    model_config = _MODEL_CONFIG

    id: UUID = Field(default_factory=new_id)
    assignment_id: UUID
    criteria: list[RubricCriterion] = Field(default_factory=list)
    leniency_note: str = (
        "Grade generously. Reward correct method even with minor arithmetic "
        "slips. Give partial credit for partial progress. Interpret ambiguous "
        "work charitably in the student's favor."
    )
    version: int = 1
    status: ArtifactStatus = ArtifactStatus.PROPOSED

    def for_problem(self, problem_id: UUID) -> list[RubricCriterion]:
        return [c for c in self.criteria if c.problem_id == problem_id]


class SubmissionAnswer(BaseModel):
    model_config = _MODEL_CONFIG

    problem_id: UUID
    work_text: str = ""              # the shown work
    final_answer: Optional[str] = None  # the extracted final answer, if any


class Submission(BaseModel):
    model_config = _MODEL_CONFIG

    id: UUID = Field(default_factory=new_id)
    assignment_id: UUID
    student_label: str               # anonymizable handle, not a real name
    answers: list[SubmissionAnswer] = Field(default_factory=list)
    sanitized: bool = False          # set True once injection-scrubbed

    def answer_for(self, problem_id: UUID) -> Optional[SubmissionAnswer]:
        return next((a for a in self.answers if a.problem_id == problem_id), None)


# --------------------------------------------------------------------------- #
# Context (decision 6) — P1 selects + budgets; P2 frames the prompt
# --------------------------------------------------------------------------- #

class GradingContext(BaseModel):
    """Everything the grader needs for ONE problem, already curated and
    budget-checked by P1.

    OWNERSHIP BOUNDARY (do not blur this):
      * P1 owns SELECTION and BUDGETING — which reference solution, which
        textbook method, how to trim, and the guarantee that estimated_tokens
        <= token_budget. This IS the context-engineering work.
      * P2 owns FRAMING — how these curated pieces are arranged into the grader
        prompt vs. the critic prompt. P2 must not need to re-fetch or re-trim.
    """
    model_config = _MODEL_CONFIG

    problem_id: UUID
    problem_statement: str
    reference_solution: str
    reference_answer: Optional[str]
    rubric_criteria: list[RubricCriterion]
    grading_policy: str              # standing policy block incl. leniency
    student_work: str                # sanitized
    student_final_answer: Optional[str]
    points_possible: float = Field(ge=0)

    token_budget: int = DEFAULT_TOKEN_BUDGET
    estimated_tokens: int            # P1 fills this in and guarantees the bound

    @model_validator(mode="after")
    def _within_budget(self) -> "GradingContext":
        if self.estimated_tokens > self.token_budget:
            raise ValueError(
                f"GradingContext for problem {self.problem_id} is "
                f"{self.estimated_tokens} tokens, over budget {self.token_budget}. "
                f"P1 must trim before handing off."
            )
        return self


class SubmissionContext(BaseModel):
    """What P1 hands to P2's grade(): the per-problem contexts for one whole
    submission (decision 2 — grade() takes the whole submission)."""
    model_config = _MODEL_CONFIG

    submission_id: UUID
    problem_contexts: list[GradingContext] = Field(default_factory=list)

    def context_for(self, problem_id: UUID) -> Optional[GradingContext]:
        return next(
            (c for c in self.problem_contexts if c.problem_id == problem_id), None
        )


# --------------------------------------------------------------------------- #
# Grades (decisions 2, 3, 4, 8)
# --------------------------------------------------------------------------- #

class ProblemGrade(BaseModel):
    """The grade for ONE problem. Domain outcomes (no answer / ungradeable) are
    represented here as data, not raised as errors (decision 8)."""
    model_config = _MODEL_CONFIG

    problem_id: UUID
    outcome: ProblemOutcome = ProblemOutcome.GRADED
    points_awarded: float = Field(ge=0)
    points_possible: float = Field(ge=0)

    answer_matched: Optional[bool] = None     # None when not applicable / no answer
    partial_credit_reason: Optional[str] = None
    evidence: str = ""                      # what in the work justified the score
    critic_agreement: Optional[bool] = None    # None if the critic didn't run

    @model_validator(mode="after")
    def _check_scoring(self) -> "ProblemGrade":
        # Round awarded points to the allowed step (decision 3).
        object.__setattr__(self, "points_awarded", round_to_step(self.points_awarded))
        if self.points_awarded > self.points_possible:
            raise ValueError(
                f"points_awarded ({self.points_awarded}) exceeds points_possible "
                f"({self.points_possible}) for problem {self.problem_id}"
            )
        if self.outcome in (ProblemOutcome.NO_ANSWER, ProblemOutcome.UNGRADEABLE):
            # Non-graded outcomes must not silently carry credit.
            if self.points_awarded != 0:
                raise ValueError(
                    f"outcome {self.outcome.value} must award 0 points "
                    f"(problem {self.problem_id})"
                )
        elif self.points_awarded < self.points_possible and not self.partial_credit_reason:
            # Partial credit on a graded problem must be justified (decision 3).
            raise ValueError(
                f"partial_credit_reason required when awarding partial credit "
                f"(problem {self.problem_id})"
            )
        return self


class Grade(BaseModel):
    """The grade for a whole submission. `status` gates usability; `escalated`
    is a flag; `resolution` + approver carry provenance (decision 4)."""
    model_config = _MODEL_CONFIG

    id: UUID = Field(default_factory=new_id)
    submission_id: UUID
    assignment_id: UUID
    problem_grades: list[ProblemGrade] = Field(default_factory=list)

    status: ArtifactStatus = ArtifactStatus.PROPOSED   # gate: usable yet?
    escalated: bool = False                            # flag: needs a human?
    resolution: GradeResolution = GradeResolution.AI_ONLY
    approver_id: Optional[str] = None
    approved_at: Optional[datetime] = None

    @property
    def total_awarded(self) -> float:
        return round(sum(g.points_awarded for g in self.problem_grades), 4)

    @property
    def total_possible(self) -> float:
        return round(sum(g.points_possible for g in self.problem_grades), 4)

    @property
    def fraction(self) -> float:
        tp = self.total_possible
        return round(self.total_awarded / tp, 4) if tp else 0.0

    @model_validator(mode="after")
    def _approval_consistency(self) -> "Grade":
        if self.status is ArtifactStatus.APPROVED and self.approver_id is None:
            raise ValueError("an approved grade must record an approver_id")
        return self


# --------------------------------------------------------------------------- #
# Trace (decision 7) — frozen envelope + open steps
# --------------------------------------------------------------------------- #

class Step(BaseModel):
    """Open on purpose. `type` is a free string (use StepKind constants); `data`
    is whatever that step needs. P2 evolves this without breaking P3."""
    model_config = ConfigDict(extra="forbid")

    type: str
    data: dict = Field(default_factory=dict)


class Trace(BaseModel):
    """The minimal envelope P3's evaluation consumes is frozen; the step list is
    soft (decision 7)."""
    model_config = _MODEL_CONFIG

    stop_reason: StopReason
    critic_agreement: Optional[bool] = None
    num_revisions: int = 0
    tokens_used: Optional[int] = None
    latency_ms: Optional[int] = None
    steps: list[Step] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Exceptions (decision 8) — real failures only; caught at the batch boundary
# --------------------------------------------------------------------------- #

class GradingError(Exception):
    """Base for genuine failures (not domain outcomes). Catch these once at the
    batch boundary and isolate per-submission so one bad submission can't kill a
    batch of 30. Retry/backoff belongs around ToolError/ModelError."""


class ParseError(GradingError):
    """Ingestion could not parse an assignment or submission."""


class ToolError(GradingError):
    """A verification tool (e.g. SymPy) failed unexpectedly."""


class ModelError(GradingError):
    """An LLM call failed, timed out, or returned unusable output."""


# --------------------------------------------------------------------------- #
# Function seams (typed stubs). Ownership in [brackets]. Build against these.
# --------------------------------------------------------------------------- #

# ---- [P1] ingestion, context, rubric -------------------------------------- #

def retrieve_method(problem: Problem) -> Optional[str]:
    """[P1] Retrieve the relevant textbook/notes method for this problem, or
    None when no corpus is loaded. Called at rubric-design time only — never at
    grading time."""
    raise NotImplementedError


def develop_solution(problem: Problem) -> Problem:
    """[P1] Return a copy of `problem` with reference_solution / reference_answer
    populated and solution_status=PROPOSED. If a sample exists, ingest and
    cross-check it; on disagreement, leave PROPOSED for human review."""
    raise NotImplementedError


def verify_solution(problem: Problem) -> tuple[bool, str]:
    """[P1] Best-effort validation of a proposed solution (self-consistency +
    substitution via P2's tool). Returns (ok, note). Does NOT approve — a human
    approves. Kept a separate function so it can move to P2 later cleanly."""
    raise NotImplementedError


def draft_rubric(assignment: Assignment, method_context: dict[UUID, Optional[str]]) -> Rubric:
    """[P1] Draft a lenient, per-problem rubric grounded in the retrieved method.
    `method_context` maps problem_id -> retrieved text (or None). Returns a
    PROPOSED rubric for human approval."""
    raise NotImplementedError


def build_context(problem: Problem, submission: Submission, rubric: Rubric) -> GradingContext:
    """[P1] Assemble the curated, budget-checked context for ONE problem
    (decision 6). Takes the approved `problem` (which carries the statement and
    the approved reference solution). P1 selects and trims; the returned object
    must satisfy estimated_tokens <= token_budget."""
    raise NotImplementedError


def build_submission_context(assignment: Assignment, submission: Submission, rubric: Rubric) -> SubmissionContext:
    """[P1] Build per-problem contexts for a whole submission (loops
    build_context over the assignment's approved problems). This is what P2's
    grade() receives."""
    raise NotImplementedError


# ---- [P2] grading agent + critic + verification tool ---------------------- #

def check_equivalence(a: str, b: str) -> bool:
    """[P2] Verification tool: are two answers mathematically equivalent
    (e.g. 'x+1' vs '1+x')? Behind the type interface; SymPy for math. Raises
    ToolError on unexpected failure."""
    raise NotImplementedError


def verify(answer: str, problem: Problem) -> bool:
    """[P2] Verify an answer against a problem where the type allows it (e.g.
    substitution). Raises ToolError on unexpected failure."""
    raise NotImplementedError


def grade(submission: Submission, rubric: Rubric, context: SubmissionContext) -> tuple[Grade, Trace]:
    """[P2] Grade a whole submission (decision 2). Internally: per problem, run
    the grader (ReAct + tool), then the critic, then reconcile (one revision,
    else escalate). Domain outcomes (no answer / ungradeable / escalated) are
    recorded in the Grade, not raised. Raises ModelError/ToolError only on
    genuine failures, for the batch boundary to isolate."""
    raise NotImplementedError


# ---- [P3] feedback, review, evaluation ------------------------------------ #

def generate_feedback(grade: Grade, rubric: Rubric) -> dict[UUID, str]:
    """[P3] Produce per-problem feedback text (problem_id -> explanation),
    grounded in each ProblemGrade's evidence and the rubric."""
    raise NotImplementedError


def answer_followup(question: str, submission_id: UUID) -> str:
    """[P3] Feedback-chat: answer a student's follow-up about their grade
    (agentic RAG over their submission, rubric, and grade)."""
    raise NotImplementedError