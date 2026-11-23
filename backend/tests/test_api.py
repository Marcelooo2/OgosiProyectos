from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def auth_headers():
    response = client.post("/api/auth/login", json={"email": "postulante@skillgrow.pe", "password": "Demo123!"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_profile_and_assessment_flow():
    headers = auth_headers()
    profiles = client.get("/api/profiles", headers=headers)
    assert profiles.status_code == 200
    profile = profiles.json()[0]
    scores = {item["name"]: 60 for item in profile["requirements"]}
    assessment = client.post("/api/assessments", headers=headers, json={"profile_id": profile["id"], "self_scores": scores})
    assert assessment.status_code == 200
    assert 0 <= assessment.json()["overall_fit"] <= 100
    assert assessment.json()["scores"]

