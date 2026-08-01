"""[P3] Label-free evaluation metrics for grading runs."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Iterable

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


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def evaluate_runs(runs: Iterable[tuple[Grade, Trace]]) -> EvaluationReport:
    """Compute objective and explicitly label-free quality indicators."""
    collected = list(runs)
    matches, grounded, critic_results = [], [], []
    totals, latencies, token_counts = [], [], []
    for grade, trace in collected:
        totals.append(grade.total_awarded)
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
    return EvaluationReport(
        runs=len(collected), answer_match_rate=_rate(matches),
        grounding_rate=_rate(grounded), critic_agreement_rate=_rate(critic_results),
        score_standard_deviation=round(pstdev(totals), 4) if totals else None,
        average_latency_ms=round(mean(latencies), 2) if latencies else None,
        average_tokens_used=round(mean(token_counts), 2) if token_counts else None,
    )
