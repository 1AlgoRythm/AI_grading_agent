"""AppTest walkthroughs for p3_app.py's real-data wiring.

Unit tests on the lane modules can't catch wiring mistakes in the Streamlit
app itself (wrong widget referenced, session state not re-persisted, etc.)
-- these drive the actual app script via Streamlit's testing framework, the
same way a human clicking through it would.
"""
from __future__ import annotations

import json

from contracts import ArtifactStatus
from lanes import p1_ingestion as p1
from lanes import p2_grading as p2
from lanes.p1_storage import P1Store
from lanes.p2_storage import P2Store


def _seed_real_data(db_url: str, *, wrong_answer: bool):
    p1_store = P1Store(db_url)
    assignment = p1.ingest_assignment("Problem A (5 points): Solve for x: 2x + 6 = 10.")
    for prob in assignment.problems:
        p1.develop_solution(prob)
        prob.solution_status = ArtifactStatus.APPROVED
    p1_store.save_assignment(assignment)

    rubric = p1.draft_rubric(assignment, {})
    rubric.status = ArtifactStatus.APPROVED
    p1_store.save_rubric(rubric)

    final_answer = "x = 3" if wrong_answer else "x = 2"
    submission = p1.ingest_submission(
        f"Problem A\nWork: 2x+6=10\nFinal answer: {final_answer}", assignment=assignment,
    )
    context = p1.build_submission_context(assignment, submission, rubric)
    grade, trace = p2.grade(submission, rubric, context)

    p2_store = P2Store(db_url)
    p2_store.save(grade, trace)
    return assignment, grade, p1_store, p2_store


def test_p3_app_defaults_to_fixtures_with_no_real_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("p3_app.py")
    at.run()

    assert "7.5/10" in at.metric[0].value  # fixtures.sample_grade()'s known total


def test_p3_app_loads_a_real_assignment_and_grade_from_the_shared_db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'p3.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    assignment, grade, _, _ = _seed_real_data(db_url, wrong_answer=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("p3_app.py")
    at.run()
    at.sidebar.selectbox[0].select(f"{assignment.label} ({len(assignment.problems)} problems)").run()
    at.sidebar.selectbox[1].select(at.sidebar.selectbox[1].options[0]).run()
    at.sidebar.button[0].click().run()

    assert f"{grade.total_awarded:g}/{grade.total_possible:g}" in at.metric[0].value


def test_p3_app_override_and_finalize_both_repersist_to_p2store(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'p3.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    assignment, grade, _, p2_store = _seed_real_data(db_url, wrong_answer=True)
    problem_grade = grade.problem_grades[0]
    assert problem_grade.points_awarded < problem_grade.points_possible  # wrong answer -> partial credit

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("p3_app.py")
    at.run()
    at.sidebar.selectbox[0].select(f"{assignment.label} ({len(assignment.problems)} problems)").run()
    at.sidebar.selectbox[1].select(at.sidebar.selectbox[1].options[0]).run()
    at.sidebar.button[0].click().run()

    at.number_input[0].set_value(float(problem_grade.points_possible)).run()
    at.text_input[1].set_value("Equivalent method accepted on manual review").run()
    at.button(key=f"FormSubmitter:override-{problem_grade.problem_id}-Save override").click().run()

    after_override = p2_store.get_grade(grade.id)
    assert after_override.total_awarded == after_override.total_possible

    approve_btn = next(b for b in at.button if b.label == "Approve final grade")
    approve_btn.click().run()

    finalized = p2_store.get_grade(grade.id)
    assert finalized.status is ArtifactStatus.APPROVED
    assert finalized.approver_id == "instructor_1"


def test_p3_app_load_demo_fixtures_button_resets_to_fixtures(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'p3.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    assignment, grade, _, _ = _seed_real_data(db_url, wrong_answer=False)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("p3_app.py")
    at.run()
    at.sidebar.selectbox[0].select(f"{assignment.label} ({len(assignment.problems)} problems)").run()
    at.sidebar.selectbox[1].select(at.sidebar.selectbox[1].options[0]).run()
    at.sidebar.button[0].click().run()
    assert "7.5/10" not in at.metric[0].value

    fixtures_btn = next(b for b in at.sidebar.button if b.label == "Load demo fixtures instead")
    fixtures_btn.click().run()
    assert "7.5/10" in at.metric[0].value


def test_p3_app_shows_a_batch_evaluation_across_an_assignments_graded_submissions(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'p3.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    p1_store = P1Store(db_url)
    assignment = p1.ingest_assignment("Problem A (5 points): Solve for x: 2x + 6 = 10.")
    for prob in assignment.problems:
        p1.develop_solution(prob)
        prob.solution_status = ArtifactStatus.APPROVED
    p1_store.save_assignment(assignment)
    rubric = p1.draft_rubric(assignment, {})
    rubric.status = ArtifactStatus.APPROVED
    p1_store.save_rubric(rubric)

    p2_store = P2Store(db_url)
    for final in ("x = 2", "x = 3"):
        submission = p1.ingest_submission(f"Problem A\nFinal answer: {final}", assignment=assignment)
        context = p1.build_submission_context(assignment, submission, rubric)
        grade, trace = p2.grade(submission, rubric, context)
        p2_store.save(grade, trace)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("p3_app.py")
    at.run()
    at.sidebar.selectbox[0].select(f"{assignment.label} ({len(assignment.problems)} problems)").run()
    at.sidebar.selectbox[1].select(at.sidebar.selectbox[1].options[0]).run()
    at.sidebar.button[0].click().run()

    batch_reports = [json.loads(j.value) for j in at.json if json.loads(j.value).get("runs", 0) > 1]
    assert batch_reports and batch_reports[0]["runs"] == 2
