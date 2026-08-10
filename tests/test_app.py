"""AppTest walkthrough for the unified app.py dispatcher.

This is the one test that actually exercises the reason app.py exists: a
real assignment uploaded and graded on the "Upload & Rubric" tab must show
up immediately on "Grade & Trace" and "Review & Feedback" with zero extra
clicks and zero DB round-trips (shared st.session_state, one process). Two
real bugs surfaced writing this test: p3_app.py bundled its own audit_log
initialization behind a `"p1_store" not in st.session_state` check that
p1_app.py's own initialization satisfied first, skipping it entirely; and
`draft_rubric` never repointed the fixture-derived Rubric's assignment_id at
the real assignment, which `generate_feedback` silently depended on.
"""
from __future__ import annotations


def test_grading_on_the_upload_tab_is_immediately_visible_on_the_other_tabs(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("app.py")
    at.run()
    assert at.sidebar.radio[0].value == "Upload & Rubric"

    at.text_area[0].set_value("HW Test\n\nProblem A (5 points): Solve for x: 2x + 6 = 10.").run()
    next(b for b in at.button if b.label == "Ingest assignment").click().run()
    next(b for b in at.button if b.label.startswith("Develop solution for")).click().run()
    next(b for b in at.button if b.label == "Approve solution").click().run()
    next(b for b in at.button if b.label == "Draft rubric").click().run()
    next(b for b in at.button if b.label == "Approve rubric").click().run()

    at.text_area[4].set_value("Problem A\nWork: 2x+6=10, x=2\nFinal answer: x = 2").run()
    next(b for b in at.button if b.label == "Ingest & grade submission").click().run()

    at.sidebar.radio[0].set_value("Grade & Trace").run()
    assert "5/5" in at.metric[0].value

    at.sidebar.radio[0].set_value("Review & Feedback").run()
    assert "5/5" in at.metric[0].value
    assert any("Problem" in m.value for m in at.markdown)

    # And switching back doesn't blow up either.
    at.sidebar.radio[0].set_value("Upload & Rubric").run()
    assert any(b.label == "Ingest assignment" for b in at.button)
