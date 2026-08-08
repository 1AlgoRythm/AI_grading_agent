"""[P2] Async batch orchestration (stretch, plan §7/§10).

Grades many submissions against the same approved rubric concurrently, with
capped concurrency and retry-with-backoff so a large batch doesn't blow
through rate limits or spend caps (plan §15 risk register: "Batch cost / rate
limits | P2 | Spend cap, capped concurrency + backoff"). Each submission is
graded in isolation -- contracts.py decision 8 reserves `GradingError` (and
its `ModelError` / `ToolError` subclasses) for genuine failures, and the
batch boundary is where they get caught so one bad submission can't take
down the rest of the batch.

This module also carries the critic-independence guard from the risk
register ("Critic just agrees (no independence) | P2 | ... track agreement
rate") and from §5's own design guard: if the critic agrees with the grader
on ~100% of judgments across a big-enough batch, that is the signal it isn't
functioning as an independent check, not a sign everything is fine.
"""

from __future__ import annotations

import asyncio
import warnings
from dataclasses import dataclass
from typing import Callable, Optional, Sequence
from uuid import UUID

from contracts import Grade, GradingError, ModelError, Rubric, Submission, SubmissionContext, Trace
from lanes.p2_engine import grade_submission

__all__ = [
    "BatchResult",
    "critic_agreement_rate",
    "check_critic_independence",
    "grade_batch",
    "grade_batch_async",
    "review_queue_order",
]

GradeFn = Callable[[Submission, Rubric, SubmissionContext], tuple[Grade, Trace]]


@dataclass(frozen=True)
class BatchResult:
    submission_id: UUID
    grade: Optional[Grade]
    trace: Optional[Trace]
    error: Optional[str]
    skipped: bool = False


async def _grade_one(
    semaphore: asyncio.Semaphore,
    submission: Submission,
    rubric: Rubric,
    context: SubmissionContext,
    grade_fn: GradeFn,
    max_retries: int,
    base_delay_seconds: float,
    spend: Optional[dict],
    max_total_tokens: Optional[int],
    spend_lock: Optional[asyncio.Lock],
) -> BatchResult:
    async with semaphore:
        if max_total_tokens is not None:
            async with spend_lock:
                if spend["tokens"] >= max_total_tokens:
                    return BatchResult(
                        submission_id=submission.id, grade=None, trace=None,
                        error=(
                            f"skipped: batch spend cap reached "
                            f"({spend['tokens']}/{max_total_tokens} tokens already spent)"
                        ),
                        skipped=True,
                    )

        attempt = 0
        while True:
            try:
                grade, trace = await asyncio.to_thread(grade_fn, submission, rubric, context)
            except ModelError as exc:
                # Transient by nature (a network call to the model provider)
                # -- worth a bounded, backed-off retry before giving up.
                if attempt >= max_retries:
                    return BatchResult(submission_id=submission.id, grade=None, trace=None, error=str(exc))
                await asyncio.sleep(base_delay_seconds * (2 ** attempt))
                attempt += 1
                continue
            except GradingError as exc:
                # ToolError and any other GradingError are not retried: a
                # SymPy crash on the same input will crash the same way
                # again, so retrying would just burn the batch's time budget.
                return BatchResult(submission_id=submission.id, grade=None, trace=None, error=str(exc))

            if max_total_tokens is not None:
                async with spend_lock:
                    spend["tokens"] += trace.tokens_used or 0
            return BatchResult(submission_id=submission.id, grade=grade, trace=trace, error=None)


async def grade_batch_async(
    submissions: Sequence[Submission],
    rubric: Rubric,
    contexts: Sequence[SubmissionContext],
    max_concurrency: int = 5,
    max_retries: int = 2,
    base_delay_seconds: float = 0.5,
    grade_fn: GradeFn = grade_submission,
    max_total_tokens: Optional[int] = None,
) -> list[BatchResult]:
    """Grade many submissions concurrently against one rubric.

    `contexts[i]` is the `SubmissionContext` P1 built for `submissions[i]`.
    Results preserve input order regardless of completion order. A
    `ModelError` on any one submission is retried up to `max_retries` times
    with exponential backoff before that submission is recorded as failed;
    `grade_fn` is overridable for testing the retry path without a real
    model provider.

    `max_total_tokens` is the plan's §15 "spend cap" mitigation, in tokens
    (the only cost proxy this codebase already tracks, via `Trace.tokens_used`
    -- there's no per-model $ pricing table to convert to a dollar figure).
    It's a soft cap: submissions already admitted past the concurrency gate
    when the cap is crossed still finish, but no new one starts once the
    running total is at or over the cap. Skipped submissions come back with
    `BatchResult.skipped=True` rather than silently vanishing from the batch.
    """
    if len(submissions) != len(contexts):
        raise ValueError("submissions and contexts must be the same length")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")

    semaphore = asyncio.Semaphore(max_concurrency)
    spend = {"tokens": 0} if max_total_tokens is not None else None
    spend_lock = asyncio.Lock() if max_total_tokens is not None else None
    tasks = [
        _grade_one(
            semaphore, submission, rubric, context, grade_fn, max_retries, base_delay_seconds,
            spend, max_total_tokens, spend_lock,
        )
        for submission, context in zip(submissions, contexts)
    ]
    results = list(await asyncio.gather(*tasks))

    warning = check_critic_independence(results)
    if warning:
        warnings.warn(warning, RuntimeWarning, stacklevel=2)

    return results


def grade_batch(
    submissions: Sequence[Submission],
    rubric: Rubric,
    contexts: Sequence[SubmissionContext],
    max_concurrency: int = 5,
    max_retries: int = 2,
    base_delay_seconds: float = 0.5,
    grade_fn: GradeFn = grade_submission,
    max_total_tokens: Optional[int] = None,
) -> list[BatchResult]:
    """Synchronous entry point for `grade_batch_async`, for non-async callers."""
    return asyncio.run(
        grade_batch_async(
            submissions, rubric, contexts, max_concurrency, max_retries, base_delay_seconds,
            grade_fn, max_total_tokens,
        )
    )


def review_queue_order(results: Sequence[BatchResult]) -> list[int]:
    """Indices into `results`, ordered so escalated and heavily-revised grades
    surface first (plan §10: "surface low-confidence / escalated grades to
    the top of the review queue"). Errored submissions sort first of all --
    a human needs to look at those before anything else."""

    def priority(item: tuple[int, BatchResult]) -> tuple[int, int, int]:
        _, result = item
        errored_rank = 0 if result.error is not None else 1
        escalated_rank = 0 if (result.grade and result.grade.escalated) else 1
        revisions_rank = -(result.trace.num_revisions if result.trace else 0)
        return (errored_rank, escalated_rank, revisions_rank)

    return [index for index, _ in sorted(enumerate(results), key=priority)]


def critic_agreement_rate(results: Sequence[BatchResult]) -> Optional[float]:
    """Fraction of individual critic judgments (across every graded problem
    in the batch) where the critic agreed with the grader. `None` when no
    critic judgment ran at all (e.g. every answer was an objective match)."""
    signals = [
        pg.critic_agreement
        for result in results
        if result.grade is not None
        for pg in result.grade.problem_grades
        if pg.critic_agreement is not None
    ]
    return round(sum(signals) / len(signals), 4) if signals else None


def check_critic_independence(
    results: Sequence[BatchResult],
    min_samples: int = 5,
    suspicious_threshold: float = 0.98,
) -> Optional[str]:
    """Guard from the plan's risk register (§5/§15): if the critic agrees
    with the grader on ~100% of judgments, it isn't functioning as an
    independent check. Returns a warning message once a batch has enough
    samples to be meaningful and crosses the threshold; `None` otherwise."""
    signals = [
        pg.critic_agreement
        for result in results
        if result.grade is not None
        for pg in result.grade.problem_grades
        if pg.critic_agreement is not None
    ]
    if len(signals) < min_samples:
        return None
    rate = sum(signals) / len(signals)
    if rate >= suspicious_threshold:
        return (
            f"Critic agreement rate is {rate:.1%} across {len(signals)} judgments in this batch -- "
            "the plan's own guard (§5) treats ~100% agreement as the sign the critic isn't "
            "independent, not as a sign everything is fine. Check the critic prompt, model, and "
            "temperature configuration (CRITIC_MODEL_NAME / CRITIC_TEMPERATURE)."
        )
    return None
