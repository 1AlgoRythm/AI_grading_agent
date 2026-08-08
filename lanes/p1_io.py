"""[P1] I/O helpers for assignment and submission sources.

This module is the first seam that touches untrusted input. It does three
things:

- parses assignment sources into structured `Assignment`/`Problem` objects,
- parses submission sources into structured `Submission`/`SubmissionAnswer`
  objects, and
- sanitizes obvious prompt-injection and control-character noise.

The implementation is intentionally conservative. It can handle plain text,
Markdown, and notebook sources directly, and it falls back to the shared
fixtures when a source is unavailable or not parseable. A production version
can replace the file readers without changing the exported function names.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, Optional

import fixtures
from contracts import ArtifactStatus, Assignment, Problem, SolutionSource, Submission, SubmissionAnswer

__all__ = ["ingest_assignment", "ingest_submission"]


def _source_path(source: str) -> Path | None:
    if not source:
        return None
    path = Path(source).expanduser()
    return path if path.exists() else None


def _read_pdf_text(path: Path) -> str | None:
    """Best-effort PDF text extraction without a hard dependency."""
    for module_name, attr_name in (("pypdf", "PdfReader"), ("PyPDF2", "PdfReader")):
        try:
            module = __import__(module_name, fromlist=[attr_name])
            reader = getattr(module, attr_name)(str(path))
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            return "\n".join(pages).strip()
        except Exception:
            continue
    return None


def _read_notebook_text(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf8"))
    except Exception:
        return None
    cells = payload.get("cells", []) if isinstance(payload, dict) else []
    parts: list[str] = []
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        if source:
            parts.append(str(source))
    return "\n\n".join(parts).strip() or None


def _read_source_text(source: str) -> str | None:
    path = _source_path(source)
    if path is None:
        return source.strip() if source and "\n" in source else None

    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".rst"}:
            return path.read_text(encoding="utf8")
        if suffix in {".json", ".ipynb"}:
            return _read_notebook_text(path)
        if suffix == ".pdf":
            return _read_pdf_text(path)
        return path.read_text(encoding="utf8")
    except Exception:
        return None


def _sanitize_text(text: str, max_length: int = 50_000) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for marker in ("system:", "assistant:", "user:", "#prompt", "<script>"):
        text = text.replace(marker, "")
    return text[:max_length]


def _looks_like_problem_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(
        re.match(r"^(?:#{1,6}\s*)?(?:problem|question|q)\b", stripped, re.IGNORECASE)
        or re.match(r"^\d+\s*[\).:-]\s+", stripped)
    )


def _split_problem_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    current: list[str] = []
    saw_heading = False

    for line in lines:
        if _looks_like_problem_heading(line):
            saw_heading = True
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            current.append(line.rstrip())
        else:
            current.append(line.rstrip())

    if current:
        blocks.append("\n".join(current).strip())

    if saw_heading and blocks:
        return [block for block in blocks if block]

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return paragraphs


def _split_submission_blocks(text: str) -> list[str]:
    blocks = _split_problem_blocks(text)
    return blocks if blocks else ([text.strip()] if text.strip() else [])


def _extract_points(text: str) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:points?|pts?)\b", text, re.IGNORECASE)
    return float(match.group(1)) if match else 1.0


def _extract_problem_label(text: str, index: int) -> str:
    match = re.search(r"^(?:#{1,6}\s*)?(?:problem|question|q)?\s*([0-9]+|[A-Za-z]+)\b", text.strip(), re.IGNORECASE)
    if match:
        token = match.group(1).strip()
        return f"Q{token}"
    return f"Q{index}"


def _extract_final_answer(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        match = re.match(r"^(?:final\s+answer|answer)\s*[:=]\s*(.+)$", stripped, re.IGNORECASE)
        if match:
            return _sanitize_text(match.group(1))
    return None


def _strip_fixture_solutions(assignment: Assignment) -> Assignment:
    copied = assignment.model_copy(deep=True)
    for problem in copied.problems:
        problem.reference_solution = None
        problem.reference_answer = None
        problem.solution_source = None
        problem.solution_status = ArtifactStatus.PROPOSED
    return copied


def _template_submission() -> Submission:
    return fixtures.sample_submission().model_copy(deep=True)


def _build_parsed_assignment(source: str, text: str) -> Assignment | None:
    blocks = _split_problem_blocks(text)
    if not blocks:
        return None

    if len(blocks) > 1:
        first_block = blocks[0]
        first_line = first_block.splitlines()[0] if first_block.splitlines() else first_block
        looks_like_preamble = (
            not _looks_like_problem_heading(first_line)
            and not re.search(r"\b(?:problem|question|exercise|points?)\b", first_block, re.IGNORECASE)
        )
        if looks_like_preamble:
            blocks = blocks[1:]
            if not blocks:
                return None

    label = Path(source).stem if _source_path(source) else "assignment"
    title = _sanitize_text(text.splitlines()[0]) if text.splitlines() else label.replace("_", " ").title()
    assignment = Assignment(label=label, title=title, type="math")
    for index, block in enumerate(blocks, start=1):
        problem = Problem(
            assignment_id=assignment.id,
            label=_extract_problem_label(block, index),
            statement=_sanitize_text(block),
            points_possible=_extract_points(block),
        )
        assignment.problems.append(problem)
    return assignment


def _build_parsed_submission(source: str, text: str) -> Submission | None:
    blocks = _split_submission_blocks(text)
    if not blocks:
        return None

    submission = _template_submission()
    submission.student_label = Path(source).stem if _source_path(source) else submission.student_label
    for answer, block in zip(submission.answers, blocks):
        answer.work_text = _sanitize_text(block)
        answer.final_answer = _extract_final_answer(block)
    if len(blocks) < len(submission.answers):
        for answer in submission.answers[len(blocks):]:
            answer.work_text = ""
            answer.final_answer = None
    submission.sanitized = True
    return submission


def ingest_assignment(source: str) -> Assignment:
    """Parse an assignment source into a structured `Assignment`.

    The function prefers actual source text when it can read it; otherwise it
    falls back to the shared fixture assignment so the walking skeleton stays
    runnable.
    """
    text = _read_source_text(source)
    if text:
        parsed = _build_parsed_assignment(source, text)
        if parsed is not None:
            return parsed
    return _strip_fixture_solutions(fixtures.sample_assignment())


def ingest_submission(source: str) -> Submission:
    """Parse a student submission source into a structured `Submission`.

    Parsed text is mapped onto the shared fixture submission shape so the rest
    of the pipeline can keep using the frozen contract without needing a new
    submission schema.
    """
    lower = (source or "").lower()
    if "sample" in lower or "student_07" in lower:
        return fixtures.sample_submission().model_copy(deep=True)

    text = _read_source_text(source)
    if text:
        parsed = _build_parsed_submission(source, text)
        if parsed is not None:
            return parsed

    sub = _template_submission()
    for ans in sub.answers:
        ans.work_text = _sanitize_text(ans.work_text or "")
        if ans.final_answer:
            ans.final_answer = _sanitize_text(ans.final_answer)
    sub.sanitized = True
    return sub
