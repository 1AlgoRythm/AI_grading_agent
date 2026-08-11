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
import re
from pathlib import Path
from typing import Optional
from uuid import UUID

import fixtures
from contracts import ArtifactStatus, Assignment, Problem, Submission, SubmissionAnswer, new_id

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


# Prompt-injection patterns to strip on ingestion. Submission text is
# untrusted (§4/§15 of the project plan) and is never treated as
# instructions downstream, but scrubbing obvious override attempts here
# means less injected content even reaches a model prompt. Patterns require
# a tag/colon delimiter so ordinary prose ("the system of equations") is
# never touched.
_INJECTION_PATTERNS = (
    r"</?\s*(system|assistant|user|human|instructions?)\s*>",  # <system>...</system>
    r"\[/?\s*(system|inst|instructions?)\s*\]",                 # [INST] / [/SYSTEM]
    r"\b(system|assistant|user|human)\s*:",                     # role-prefixed lines
    r"ignore\s+(?:\w+\s+){0,3}instructions\b",
    r"disregard\s+(?:\w+\s+){0,3}(rubric|instructions)\b",
    r"you\s+are\s+now\s+(in|acting\s+as)\b",
    r"you\s+must\s+(now\s+)?(output|award|give|return)\b",
    r"as\s+(the|an?)\s+(grading\s+)?(ai|assistant|model|grader)\b",
    r"#\s*prompt\b",
)
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def _sanitize_text(text: str, max_length: int = 50_000) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<\s*script\b[^>]*>", " ", text, flags=re.IGNORECASE)
    text = _INJECTION_RE.sub("[redacted]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length]


def _looks_like_problem_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(
        re.match(r"^(?:#{1,6}\s*)?(?:problem|question|q)\b", stripped, re.IGNORECASE)
        or re.match(r"^\d+\s*[\).:-]\s+", stripped)
    )


def _split_problem_blocks(text: str) -> tuple[list[str], bool]:
    """Returns (blocks, saw_heading) -- callers that decide whether a leading
    block is a preamble need to know whether splitting actually happened on
    real heading lines, not on the blank-line paragraph fallback, where a
    "first block with no heading words" is just as likely to be problem 1
    itself as it is a preamble."""
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
        return [block for block in blocks if block], True

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return paragraphs, False


def _split_submission_blocks(text: str) -> list[str]:
    blocks, _saw_heading = _split_problem_blocks(text)
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


def _build_parsed_assignment(source: str, text: str, assignment_type: str = "math") -> Assignment | None:
    blocks, saw_heading = _split_problem_blocks(text)
    if not blocks:
        return None

    # Only a heading-based split lets us tell a preamble apart from problem
    # 1: every real block there starts with a detected heading line by
    # construction, so a leading block that doesn't is provably pre-heading
    # content. The blank-line paragraph fallback (no headings found at all)
    # has no such signal -- treating its first paragraph as a discardable
    # preamble was silently dropping a real, headerless first problem.
    if saw_heading and len(blocks) > 1:
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
    assignment = Assignment(label=label, title=title, type=assignment_type)
    for index, block in enumerate(blocks, start=1):
        problem = Problem(
            assignment_id=assignment.id,
            label=_extract_problem_label(block, index),
            statement=_sanitize_text(block),
            points_possible=_extract_points(block),
        )
        assignment.problems.append(problem)
    return assignment


def _resolve_problem_id(block: str, index: int, assignment: Optional[Assignment]) -> Optional[UUID]:
    """Map a parsed submission block to a real problem id when the target
    assignment is known: match by extracted label first (e.g. a block
    literally headed 'Problem 2' -> the assignment's 'Q2'), falling back to
    position when the label doesn't line up. Without an assignment, this
    just returns a fresh placeholder id -- the caller is responsible for
    remapping it once it knows which assignment the submission belongs to.

    Returns None when the assignment IS known but this block matches none of
    its problems by label and its position also runs past the number of
    real problems -- typically a stray heading-less paragraph the fallback
    in `_split_problem_blocks` split out beyond the assignment's problem
    count. Returning a freshly minted id here used to be the bug: it built
    an answer for a problem_id that would never exist in `assignment.problems`,
    which crashed `build_submission_context` with a `KeyError` far downstream
    instead of failing where the mismatch actually happened.
    """
    if assignment is None or not assignment.problems:
        return new_id()
    label = _extract_problem_label(block, index)
    for problem in assignment.problems:
        if problem.label == label:
            return problem.id
    if index - 1 < len(assignment.problems):
        return assignment.problems[index - 1].id
    return None


def _build_parsed_submission(source: str, text: str, assignment: Optional[Assignment] = None) -> Submission | None:
    blocks = _split_submission_blocks(text)
    if not blocks:
        return None

    label = Path(source).stem if _source_path(source) else "student"
    answers: list[SubmissionAnswer] = []
    for index, block in enumerate(blocks, start=1):
        problem_id = _resolve_problem_id(block, index, assignment)
        if problem_id is None:
            # No real problem to attach this to -- fold it into the previous
            # answer's shown work instead of manufacturing a problem_id no
            # assignment will ever have. There's always a previous answer by
            # this point: an in-range block always resolves to a real id above.
            previous = answers[-1]
            previous.work_text = _sanitize_text(f"{previous.work_text} {block}")
            if previous.final_answer is None:
                previous.final_answer = _extract_final_answer(block)
            continue
        answers.append(SubmissionAnswer(
            problem_id=problem_id,
            work_text=_sanitize_text(block),
            final_answer=_extract_final_answer(block),
        ))
    if not answers:
        return None
    return Submission(
        assignment_id=assignment.id if assignment else new_id(),
        student_label=label,
        answers=answers,
        sanitized=True,
    )


def ingest_assignment(source: str, assignment_type: str = "math") -> Assignment:
    """Parse an assignment source into a structured `Assignment`.

    The function prefers actual source text when it can read it; otherwise it
    falls back to the shared fixture assignment so the walking skeleton stays
    runnable. `assignment_type` selects the verification tool P2 uses at
    grading time (plan §4) -- "math" gets the SymPy-backed objective check;
    other registered types (e.g. "short_answer", "proof") have none, so
    grading leans on the critic and human review instead.
    """
    text = _read_source_text(source)
    if text:
        parsed = _build_parsed_assignment(source, text, assignment_type)
        if parsed is not None:
            return parsed
    return _strip_fixture_solutions(fixtures.sample_assignment())


def _remap_answers_to_assignment(sub: Submission, assignment: Assignment) -> None:
    """Point a fixture/template stand-in's answers at real problems in
    `assignment`, the same way `_resolve_problem_id` maps real parsed
    blocks. Without this, a stand-in submission kept whichever problem_ids
    the fixture it was copied from happened to carry -- ids that belong to
    a *different* Assignment object and don't exist in `assignment.problems`,
    which crashed `build_submission_context` with a `KeyError`. An answer
    that doesn't map to any real problem (more stand-in answers than the
    assignment has problems) is dropped rather than kept under a fake id."""
    remapped: list[SubmissionAnswer] = []
    for index, ans in enumerate(sub.answers, start=1):
        resolved = _resolve_problem_id(ans.work_text or "", index, assignment)
        if resolved is None:
            continue
        ans.problem_id = resolved
        remapped.append(ans)
    sub.answers = remapped


def ingest_submission(source: str, assignment: Optional[Assignment] = None) -> Submission:
    """Parse a student submission source into a structured `Submission`.

    Each parsed block becomes its own answer (not a fixed-size clone of the
    demo fixture's two answers), so this works for assignments with any
    number of problems. When the target `assignment` is known, each answer
    is mapped to a real `problem_id` (by extracted label, falling back to
    position) and `assignment_id` is set to the real assignment -- pass it
    whenever you have it. Without it, ids are placeholders the caller must
    remap once it knows which assignment the submission belongs to.
    """
    text = _read_source_text(source)
    if text:
        parsed = _build_parsed_submission(source, text, assignment)
        if parsed is not None:
            return parsed

    # No real, parseable content -- fall back to a stand-in. Matched against
    # the source's file stem only, never against submission prose: matching
    # "sample" anywhere in a real student's pasted text (e.g. "for example",
    # "sample size" in a stats problem) used to discard their actual
    # submission and silently substitute the packaged fixture answers.
    stem = Path(source).stem.lower() if source else ""
    if "sample" in stem or "student_07" in stem:
        sub = fixtures.sample_submission().model_copy(deep=True)
    else:
        sub = _template_submission()

    if assignment is not None:
        sub.assignment_id = assignment.id
        _remap_answers_to_assignment(sub, assignment)
    for ans in sub.answers:
        ans.work_text = _sanitize_text(ans.work_text or "")
        if ans.final_answer:
            ans.final_answer = _sanitize_text(ans.final_answer)
    sub.sanitized = True
    return sub
