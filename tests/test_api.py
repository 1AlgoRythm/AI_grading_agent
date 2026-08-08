from fastapi.testclient import TestClient
import fixtures as f
from api import app

client = TestClient(app)

def test_health(): assert client.get("/health").json() == {"status": "ok"}

def test_feedback_endpoint():
    response = client.post("/feedback", json={"grade": f.sample_grade().model_dump(mode="json"),
                                              "rubric": f.sample_rubric().model_dump(mode="json")})
    assert response.status_code == 200
    assert "Score: 2.5/5" in response.json()[str(f.Q2)]
