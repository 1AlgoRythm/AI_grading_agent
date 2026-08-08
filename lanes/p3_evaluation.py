"""[P3] Label-free evaluation metrics for grading runs."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Callable, Iterable

from contracts import Grade, Trace


@dataclass(frozen=True)
class EvaluationReport:
    runs: int
    answer_match_rate: float | None
    grounding_rate: float | None
    critic_agreement_rate: float | None
    score_standard_deviation: float | None
    average_latency_ms: float | None
    average_tokens_used: float | None
    reliability_by_submission: dict[str, float]
    feedback_quality_score: float | None
    injection_robustness_rate: float | None


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def evaluate_runs(runs: Iterable[tuple[Grade, Trace]], feedback_samples: Iterable[str] | None = None,
                  feedback_judge: Callable[[str], float] | None = None,
                  injection_results: Iterable[bool] | None = None) -> EvaluationReport:
    """Compute objective and explicitly label-free quality indicators."""
    collected = list(runs)
    matches, grounded, critic_results = [], [], []
    totals, latencies, token_counts = [], [], []
    totals_by_submission: dict[str, list[float]] = {}
    for grade, trace in collected:
        totals.append(grade.total_awarded)
        totals_by_submission.setdefault(str(grade.submission_id), []).append(grade.total_awarded)
        for problem_grade in grade.problem_grades:
            if problem_grade.answer_matched is not None:
                matches.append(problem_grade.answer_matched)
            grounded.append(bool(problem_grade.evidence.strip()))
            if problem_grade.critic_agreement is not None:
                critic_results.append(problem_grade.critic_agreement)
        if trace.latency_ms is not None:
            latencies.append(trace.latency_ms)
        if trace.tokens_used is not None:
            token_counts.append(trace.tokens_used)
    if not critic_results:
        critic_results = [
            trace.critic_agreement for _, trace in collected
            if trace.critic_agreement is not None
        ]
    reliability = {key: round(pstdev(scores), 4) if len(scores) > 1 else 0.0
                   for key, scores in totals_by_submission.items()}
    judged = [feedback_judge(text) for text in (feedback_samples or [])] if feedback_judge else []
    injections = list(injection_results or [])
    return EvaluationReport(
        runs=len(collected), answer_match_rate=_rate(matches),
        grounding_rate=_rate(grounded), critic_agreement_rate=_rate(critic_results),
        score_standard_deviation=round(pstdev(totals), 4) if totals else None,
        average_latency_ms=round(mean(latencies), 2) if latencies else None,
        average_tokens_used=round(mean(token_counts), 2) if token_counts else None,
        reliability_by_submission=reliability,
        feedback_quality_score=round(mean(judged), 4) if judged else None,
        injection_robustness_rate=_rate(injections),
    )


def judge_feedback_quality(text: str) -> float:
    """Reproducible four-part judge: specific, consistent, grounded, actionable."""
    lowered = text.lower()
    checks = ["score:" in lowered,
              any(x in lowered for x in ("full credit", "partial credit", "no answer")),
              "evidence:" in lowered,
              any(x in lowered for x in ("next step:", "keep using", "review "))]
    return sum(checks) / len(checks)
