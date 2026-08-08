"""FastAPI surface for P3 feedback and evaluation."""
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException
from contracts import Grade, Rubric, Trace
from lanes.p3_evaluation import evaluate_runs, judge_feedback_quality
from lanes.p3_feedback import generate_feedback

app = FastAPI(title="AI Grading Agent", version="0.1.0")

class FeedbackRequest(BaseModel):
    grade: Grade
    rubric: Rubric

class EvaluationRequest(BaseModel):
    runs: list[tuple[Grade, Trace]]
    feedback_samples: list[str] = Field(default_factory=list)
    injection_results: list[bool] = Field(default_factory=list)

@app.get("/health")
def health() -> dict[str, str]: return {"status": "ok"}

@app.post("/feedback")
def feedback(request: FeedbackRequest) -> dict[str, str]:
    try: result = generate_feedback(request.grade, request.rubric)
    except ValueError as exc: raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {str(key): value for key, value in result.items()}

@app.post("/evaluation")
def evaluation(request: EvaluationRequest) -> dict:
    return evaluate_runs(request.runs, request.feedback_samples, judge_feedback_quality,
                         request.injection_results).__dict__
