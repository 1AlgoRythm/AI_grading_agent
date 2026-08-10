"""AppTest walkthrough for p2_app.py's override wiring.

Unit tests on the lane modules can't catch wiring mistakes in the Streamlit
app itself -- this is exactly the layer where the override button used to
skip the audit log, never clear `escalated`, and corrupt the evidence field
by appending to it on every re-override. Drives the actual app script via
Streamlit's testing framework, the same way a human clicking through it
would.
"""
from __future__ import annotations

from pathlib import Path

import fixtures as f
from lanes.p2_storage import P2Store
from lanes.p3_storage import P3Store

P2_APP_PATH = str(Path(__file__).resolve().parent.parent / "p2_app.py")


def test_p2_app_override_writes_one_clean_audit_entry_and_repersists(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'p2.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(P2_APP_PATH)
    at.run()

    grade = at.session_state.p2_grade
    problem_grade_before = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q2)
    assert problem_grade_before.points_awarded < problem_grade_before.points_possible  # partial credit to start

    # Widget order: Reviewer ID text_input[0], then per problem panel
    # (Q1 first, Q2 second): number_input + "Reason for override" text_input.
    at.number_input[1].set_value(float(problem_grade_before.points_possible)).run()
    at.text_input[2].set_value("Equivalent method accepted on manual review").run()
    at.button(key=f"FormSubmitter:p2-override-{f.Q2}-Save override").click().run()

    p2_store = P2Store(db_url)
    reloaded = p2_store.get_grade(grade.id)
    overridden = next(pg for pg in reloaded.problem_grades if pg.problem_id == f.Q2)

    assert overridden.points_awarded == overridden.points_possible
    # `evidence` is the AI's original grading evidence -- overriding a score
    # must never touch it. The reason lives only in the audit log entry.
    assert overridden.evidence == problem_grade_before.evidence
    assert "Human override" not in overridden.evidence
    assert "Equivalent method accepted on manual review" not in overridden.evidence

    audit_log = at.session_state.audit_log
    assert isinstance(audit_log, P3Store)
    entries = audit_log.for_grade(reloaded.id)
    assert len(entries) == 1
    assert entries[0].reason == "Equivalent method accepted on manual review"


def test_p2_app_override_requires_a_reason(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'p2.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(P2_APP_PATH)
    at.run()
    grade = at.session_state.p2_grade
    problem_grade = next(pg for pg in grade.problem_grades if pg.problem_id == f.Q2)

    at.number_input[1].set_value(float(problem_grade.points_possible)).run()
    at.button(key=f"FormSubmitter:p2-override-{f.Q2}-Save override").click().run()

    assert any("reason" in e.value.lower() for e in at.error)
