"""Walking skeleton — the thinnest end-to-end path, wired through every seam.

It does almost nothing real (trivial stubs + fixtures). Its job is to PROVE the
three lanes connect and to give the team one shared, always-running spine. As
each lane replaces its stub bodies with real logic, this path stays green.

Run from inside the grading_agent/ directory:

    pip install -r requirements.txt
    python skeleton.py
"""

from __future__ import annotations

import os

from contracts import ArtifactStatus
from lanes import p1_ingestion as p1
from lanes import p1_rag
from lanes import p2_grading as p2
from lanes import p3_feedback as p3
from lanes.p1_storage import P1Store
from lanes.p3_review import InMemoryAuditLog, override_problem_score


def stage(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    p1_store = P1Store(os.getenv("DATABASE_URL", "sqlite:///p1_demo.db"))

    # 0. SYNC TEXTBOOK INDEX (P1) -- persist the on-disk corpus into the DB-
    # backed textbook_index table so it's durable and queryable, regardless
    # of whether a vector store happens to be installed.
    stage("0. Sync textbook index to the database  [P1]")
    chunks_indexed = p1_rag.sync_textbook_index(p1_store)
    print(f"  indexed {chunks_indexed} textbook chunk(s) into the textbook_index table")

    # 1. INGEST (P1)
    stage("1. Ingest assignment  [P1]")
    assignment = p1.ingest_assignment("uploads/hw3.pdf")
    print(f"  parsed {len(assignment.problems)} problems from '{assignment.label}'")

    # 2. RETRIEVE METHOD (P1) -- done once, up front, and reused below to
    # ground BOTH the solution and the rubric (plan §4: "generate a model
    # solution grounded in the retrieved method"; §6: retrieved only here,
    # at design time, never at grading time).
    stage("2. Retrieve course method per problem  [P1]")
    method_context = {}
    for prob in assignment.problems:
        snippet = p1.retrieve_method(prob.statement)
        method_context[prob.id] = snippet
        print(f"  {prob.label}: retrieved method -> {snippet[:60] + '...' if snippet else None!r}")

    # 3. DEVELOP + APPROVE SOLUTION (P1 + human gate)
    stage("3. Develop & approve model solution  [P1 + human]")
    for prob in assignment.problems:
        p1.develop_solution(prob, method_context=method_context.get(prob.id))
        ok, note = p1.verify_solution(prob)
        print(f"  {prob.label}: proposed solution -> answer = {prob.reference_answer!r}")
        print(f"    verify_solution: ok={ok} — {note}")
        prob.solution_status = ArtifactStatus.APPROVED          # human approves (verify_solution never auto-approves)
    print("  [human] all solutions APPROVED")
    p1_store.save_assignment(assignment)
    print(f"  persisted assignment '{assignment.label}' ({len(assignment.problems)} problems) to the database")

    # 4. DRAFT & APPROVE RUBRIC (P1 + human gate)
    stage("4. Draft & approve rubric  [P1 + human]")
    rubric = p1.draft_rubric(assignment, method_context=method_context)
    print(f"  drafted rubric v{rubric.version}, {len(rubric.criteria)} criteria [{rubric.status.value}]")
    rubric.status = ArtifactStatus.APPROVED                     # human approves
    print("  [human] rubric APPROVED")
    p1_store.save_rubric(rubric)
    print(f"  persisted rubric v{rubric.version} ({len(rubric.criteria)} criteria) to the database")

    # 5. INGEST SUBMISSION (P1)
    stage("5. Ingest submission  [P1]")
    submission = p1.ingest_submission("uploads/student_07.pdf")
    print(f"  {submission.student_label}: {len(submission.answers)} answers, sanitized={submission.sanitized}")

    # 6. BUILD CONTEXT (P1)
    stage("6. Build grading context  [P1]")
    context = p1.build_submission_context(assignment, submission, rubric)
    for c in context.problem_contexts:
        print(f"  problem {c.problem_id.hex[-2:]}: ~{c.estimated_tokens} tokens (budget {c.token_budget})")

    # 7. GRADE — grader (+ critic, later)  (P2)
    stage("7. Grade  [P2]")
    grade, trace = p2.grade(submission, rubric, context)
    for pg in grade.problem_grades:
        print(f"  problem {pg.problem_id.hex[-2:]}: {pg.points_awarded}/{pg.points_possible} [{pg.outcome.value}]")
    print(f"  total {grade.total_awarded}/{grade.total_possible} ({grade.fraction:.0%})"
          f"  | trace: stop={trace.stop_reason.value}, steps={len(trace.steps)}")

    # 8. FEEDBACK (P3)
    stage("8. Generate feedback  [P3]")
    feedback = p3.generate_feedback(grade, rubric)
    p3.register_feedback_context(grade, rubric)
    for text in feedback.values():
        print(f"  {text}")

    # 9. HUMAN REVIEW + FINALIZE (P3 + human gate)
    stage("9. Human review & finalize  [P3 + human]")
    print(f"  status before: {grade.status.value}")
    if grade.escalated:
        # Expected, designed behavior (§5): the critic and grader can
        # genuinely disagree even after the one allowed revision, and
        # finalize() correctly refuses to auto-approve that -- it needs an
        # actual human decision first. This script simulates that decision
        # (a human reviewing the trace and confirming the AI's proposed
        # score) via the same override path the UIs use, so the walking
        # skeleton demonstrates the full nine-stage path end to end instead
        # of stopping short whenever the critic disagrees.
        print("  grade ESCALATED: critic and grader did not reconcile after the bounded revision round.")
        audit = InMemoryAuditLog()
        for pg in grade.problem_grades:
            if pg.critic_agreement is False:
                print(f"  [human] reviewing problem {pg.problem_id.hex[-2:]}: "
                      f"confirming the proposed score {pg.points_awarded:g}/{pg.points_possible:g}")
                override_problem_score(
                    grade, pg.problem_id, pg.points_awarded, "instructor_1",
                    "Auto-resolved in skeleton demo: reviewed the trace and confirmed the proposed score.",
                    audit,
                )
        print(f"  [human] escalation resolved (escalated={grade.escalated})")
    p3.finalize(grade, approver_id="instructor_1")
    print(f"  status after:  {grade.status.value}  (by {grade.approver_id}, {grade.resolution.value})")

    stage("DONE — end-to-end path executed")
    print("  Every seam crossed: ingest -> solution -> rubric -> context -> "
          "grade -> feedback -> approve (resolving any escalation along the way).")
    print("  Replace stub bodies with real logic; this path stays green.")


if __name__ == "__main__":
    main()
