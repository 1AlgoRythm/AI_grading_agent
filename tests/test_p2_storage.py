"""Tests for P2's own database tables (plan §8: "grades, grade_traces,
critic_results")."""

from __future__ import annotations

from lanes.p2_storage import P2Store
from lanes import p2_grading as p2
import fixtures as f


def test_save_and_reload_a_grade_and_its_trace(tmp_path):
    store = P2Store(f"sqlite:///{tmp_path / 'p2.db'}")
    submission = f.sample_submission()
    grade, trace = p2.grade(submission, f.sample_rubric(), f.sample_submission_context())

    store.save(grade, trace)

    reloaded_grade = store.get_grade(grade.id)
    reloaded_trace = store.get_trace(grade.id)

    assert reloaded_grade is not None and reloaded_grade.id == grade.id
    assert reloaded_grade.total_awarded == grade.total_awarded
    assert reloaded_trace is not None
    assert reloaded_trace.stop_reason == trace.stop_reason
    assert reloaded_trace.num_revisions == trace.num_revisions
    assert len(reloaded_trace.steps) == len(trace.steps)


def test_get_grade_and_trace_are_none_for_an_unknown_id(tmp_path):
    store = P2Store(f"sqlite:///{tmp_path / 'p2.db'}")
    assert store.get_grade(f.GID) is None
    assert store.get_trace(f.GID) is None


def test_grades_for_submission_finds_every_saved_grade(tmp_path):
    store = P2Store(f"sqlite:///{tmp_path / 'p2.db'}")
    submission = f.sample_submission()
    grade, trace = p2.grade(submission, f.sample_rubric(), f.sample_submission_context())
    store.save(grade, trace)

    results = store.grades_for_submission(submission.id)

    assert len(results) == 1
    assert results[0].id == grade.id


def test_critic_results_are_extracted_from_critique_steps(tmp_path):
    store = P2Store(f"sqlite:///{tmp_path / 'p2.db'}")
    submission = f.sample_submission()
    grade, trace = p2.grade(submission, f.sample_rubric(), f.sample_submission_context())
    store.save(grade, trace)

    critic_rows = store.critic_results_for(grade.id)
    critique_steps = [s for s in trace.steps if s.type == "critique"]

    assert len(critic_rows) == len(critique_steps)
    assert all(row.problem_id == str(f.Q2) for row in critic_rows)  # Q1 is a tool match, no critique


def test_re_saving_a_grade_updates_rather_than_duplicates(tmp_path):
    store = P2Store(f"sqlite:///{tmp_path / 'p2.db'}")
    submission = f.sample_submission()
    grade, trace = p2.grade(submission, f.sample_rubric(), f.sample_submission_context())
    store.save(grade, trace)

    grade.problem_grades[0].evidence += " Re-checked."
    store.save(grade, trace)

    reloaded = store.get_grade(grade.id)
    assert reloaded.problem_grades[0].evidence.endswith("Re-checked.")
    assert len(store.grades_for_submission(submission.id)) == 1
