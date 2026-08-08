"""Walking skeleton — the thinnest end-to-end path, wired through every seam.

It does almost nothing real (trivial stubs + fixtures). Its job is to PROVE the
three lanes connect and to give the team one shared, always-running spine. As
each lane replaces its stub bodies with real logic, this path stays green.

Run from inside the grading_agent/ directory:

    pip install -r requirements.txt
    python skeleton.py
"""

from __future__ import annotations

from contracts import ArtifactStatus
from lanes import p1_ingestion as p1
from lanes import p2_grading as p2
from lanes import p3_feedback as p3


def stage(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    # 1. INGEST (P1)
    stage("1. Ingest assignment  [P1]")
    assignment = p1.ingest_assignment("uploads/hw3.pdf")
    print(f"  parsed {len(assignment.problems)} problems from '{assignment.label}'")

    # 2. DEVELOP + APPROVE SOLUTION (P1 + human gate)
    stage("2. Develop & approve model solution  [P1 + human]")
    for prob in assignment.problems:
        p1.develop_solution(prob)
        print(f"  {prob.label}: proposed solution -> answer = {prob.reference_answer!r}")
        prob.solution_status = ArtifactStatus.APPROVED          # human approves
    print("  [human] all solutions APPROVED")

    # 3. DRAFT + APPROVE RUBRIC (P1 + human gate)
    stage("3. Draft & approve rubric  [P1 + human]")
    rubric = p1.draft_rubric(assignment, method_context={})
    print(f"  drafted rubric v{rubric.version}, {len(rubric.criteria)} criteria [{rubric.status.value}]")
    rubric.status = ArtifactStatus.APPROVED                     # human approves
    print("  [human] rubric APPROVED")

    # 4. INGEST SUBMISSION (P1)
    stage("4. Ingest submission  [P1]")
    submission = p1.ingest_submission("uploads/student_07.pdf")
    print(f"  {submission.student_label}: {len(submission.answers)} answers, sanitized={submission.sanitized}")

    # 5. BUILD CONTEXT (P1)
    stage("5. Build grading context  [P1]")
    context = p1.build_submission_context(assignment, submission, rubric)
    for c in context.problem_contexts:
        print(f"  problem {c.problem_id.hex[-2:]}: ~{c.estimated_tokens} tokens (budget {c.token_budget})")

    # 6. GRADE — grader (+ critic, later)  (P2)
    stage("6. Grade  [P2]")
    grade, trace = p2.grade(submission, rubric, context)
    for pg in grade.problem_grades:
        print(f"  problem {pg.problem_id.hex[-2:]}: {pg.points_awarded}/{pg.points_possible} [{pg.outcome.value}]")
    print(f"  total {grade.total_awarded}/{grade.total_possible} ({grade.fraction:.0%})"
          f"  | trace: stop={trace.stop_reason.value}, steps={len(trace.steps)}")

    # 7. FEEDBACK (P3)
    stage("7. Generate feedback  [P3]")
    feedback = p3.generate_feedback(grade, rubric)
    p3.register_feedback_context(grade, rubric)
    for text in feedback.values():
        print(f"  {text}")

    # 8. HUMAN REVIEW + FINALIZE (P3 + human gate)
    stage("8. Human review & finalize  [P3 + human]")
    print(f"  status before: {grade.status.value}")
    p3.finalize(grade, approver_id="instructor_1")
    print(f"  status after:  {grade.status.value}  (by {grade.approver_id}, {grade.resolution.value})")

    stage("DONE — end-to-end path executed")
    print("  Every seam crossed: ingest -> solution -> rubric -> context -> "
          "grade -> feedback -> approve.")
    print("  Replace stub bodies with real logic; this path stays green.")


if __name__ == "__main__":
    main()
