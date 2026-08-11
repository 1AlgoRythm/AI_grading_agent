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

from pathlib import Path

# AppTest.from_file's relative-path resolution has changed across Streamlit
# versions (resolved against cwd in some, against the calling file's
# directory in others) -- use an absolute path so this doesn't depend on
# which Streamlit version pip happens to resolve.
APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")


def _grade_one_simple_submission(at) -> None:
    """Drive the "Upload & Rubric" tab through ingest -> solve -> rubric ->
    grade for one trivial problem, ending with a graded submission in
    st.session_state.last_grade. Shared by every test below that needs a
    graded submission as its starting point."""
    assert at.sidebar.radio[0].value == "Upload & Rubric"

    at.text_area[0].set_value("HW Test\n\nProblem A (5 points): Solve for x: 2x + 6 = 10.").run()
    next(b for b in at.button if b.label == "Ingest assignment").click().run()
    # Both of these touch lanes/p1_rag.py's textbook retrieval, which cold-starts
    # chromadb + onnxruntime the first time it runs in a process (2-4s) -- comfortably
    # past AppTest's 3s default `run()` timeout on a loaded CI box, which is exactly
    # what made this test intermittently fail in CI with no code-path bug at all.
    next(b for b in at.button if b.label.startswith("Develop solution for")).click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve solution").click().run()
    next(b for b in at.button if b.label == "Draft rubric").click().run(timeout=30)
    next(b for b in at.button if b.label == "Approve rubric").click().run()

    # Located by label, not a fixed index -- richer rubrics now add one
    # "Description" text_area per criterion (and the count is LLM-decided per
    # problem), which shifts a hardcoded index like `at.text_area[4]` onto a
    # criterion field instead of the submission box. That silently left the
    # submission box empty (so the grade button stayed disabled and nothing
    # was ever graded) with no exception raised -- exactly what made this
    # test's "5/5" assertion fail against a stale demo-fixture grade instead.
    next(ta for ta in at.text_area if ta.label == "...or paste the submission text directly").set_value(
        "Problem A\nWork: 2x+6=10, x=2\nFinal answer: x = 2"
    ).run()
    next(b for b in at.button if b.label == "Ingest & grade submission").click().run()


def test_grading_on_the_upload_tab_is_immediately_visible_on_the_other_tabs(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _grade_one_simple_submission(at)

    at.sidebar.radio[0].set_value("Grade & Trace").run()
    assert "5/5" in at.metric[0].value

    at.sidebar.radio[0].set_value("Review & Feedback").run()
    assert "5/5" in at.metric[0].value
    assert any("Problem" in m.value for m in at.markdown)

    # And switching back doesn't blow up either.
    at.sidebar.radio[0].set_value("Upload & Rubric").run()
    assert any(b.label == "Ingest assignment" for b in at.button)


def test_overriding_a_score_on_the_grade_tab_is_immediately_visible_on_review(tmp_path, monkeypatch):
    """session_cache.shared_grade() is what makes this work: p2_app.py and
    p3_app.py used to each hold their own independent copy of "the current
    grade" once either one loaded from the database directly, so an override
    made here wouldn't show up over there without a manual reload -- even
    though the database itself was already correct."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP_PATH)
    at.run()
    _grade_one_simple_submission(at)

    at.sidebar.radio[0].set_value("Grade & Trace").run()
    at.number_input[0].set_value(5.0).run()
    next(ti for ti in at.text_input if ti.label == "Reason for override").set_value("actually correct").run()
    next(b for b in at.button if b.label == "Save override").click().run()
    assert "5/5" in at.metric[0].value

    at.sidebar.radio[0].set_value("Review & Feedback").run()
    assert "5/5" in at.metric[0].value
