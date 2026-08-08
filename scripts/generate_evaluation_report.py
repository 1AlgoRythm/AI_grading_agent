"""Generate the checked-in label-free evaluation artifact."""
import json
import sys
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fixtures
from lanes.p3_evaluation import evaluate_runs, judge_feedback_quality
from lanes.p3_feedback import generate_feedback

grade, trace, rubric = fixtures.sample_grade(), fixtures.sample_trace(), fixtures.sample_rubric()
runs = [(grade.model_copy(deep=True), trace.model_copy(deep=True)) for _ in range(3)]
feedback = list(generate_feedback(grade, rubric).values())
report = evaluate_runs(runs, feedback, judge_feedback_quality, [True, True, True])
Path("evaluation_report.json").write_text(json.dumps(asdict(report), indent=2, sort_keys=True) + "\n")
