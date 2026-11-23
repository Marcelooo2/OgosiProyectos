from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.questionnaire import BANK, diagnostic_score, summarize

client = TestClient(app)


def signup():
    data = {"email": f"qa-{uuid4().hex}@example.org", "full_name": "Prueba funcional",
            "password": uuid4().hex, "consent": True}
    result = client.post("/api/auth/register", json=data)
    assert result.status_code == 201, result.text
    return data, {"Authorization": "Bearer " + result.json()["access_token"]}


def test_consent_revocation_invalidates_every_previous_token():
    data, old = signup()
    assert client.get("/api/admin/catalog", headers=old).status_code == 403
    assert client.post("/api/me/consent/revoke", headers=old).status_code == 200
    auth = {k:data[k] for k in ("email","password")}
    assert client.post("/api/auth/login", json=auth).status_code == 403
    assert client.post("/api/auth/login", json={**auth,"renew_consent":True}).status_code == 200
    assert client.get("/api/me", headers=old).status_code == 401
    assert client.post("/api/auth/register", json={**data,"consent":False}).status_code == 422


@pytest.mark.parametrize("declared,verified,cv,level,gap,label", [
    (60,40,100,46,42.5,"Faltante"), (None,None,100,None,None,"No evidenciada"),
    (60,None,None,60,25,"Declarada (provisional)"), (None,80,None,80,0,"Fuerte"),
    (None,60,None,60,25,"Moderada"), (None,0,None,0,100,"Faltante"),
])
def test_documented_level(declared,verified,cv,level,gap,label):
    requirement = SimpleNamespace(competency_id=1,weight=3,required_level=4,competency=SimpleNamespace(name="Python",category="Técnica"))
    row = diagnostic_score(requirement, {"self":declared,"quiz":verified,"cv":cv})
    assert (row["evidence"],row["gap"],row["status"]) == (level,gap,label)
    total = summarize([row])
    assert total["partial"] == (level is None)
    assert total["global_gap"] == gap


def test_candidate_journey_and_account_isolation():
    _, headers = signup()
    _, other = signup()
    profile = client.get("/api/profiles",headers=headers).json()[0]
    profile_id = profile["id"]
    quiz = f"/api/profiles/{profile_id}/questionnaire"
    assert client.post("/api/evidence/cv",headers=headers,files={"file":("cv.pdf",b"unreadable")}).status_code == 422
    cv = client.post("/api/evidence/cv",headers=headers,files={"file":("cv.txt",b"Programo en Python y SQL.")}).json()
    ids = [r["competency_id"] for r in cv["detected"]]
    assert ids and all(not r["confirmed"] for r in cv["detected"])
    endpoint = f"/api/evidence/cv/{cv['evidence_id']}/confirmation"
    assert client.put(endpoint,headers=other,json={"confirmed_ids":ids}).status_code == 404
    assert client.put(endpoint,headers=headers,json={"confirmed_ids":ids}).status_code == 200
    empty = client.post("/api/assessments",headers=headers,json={"profile_id":profile_id}).json()
    assert empty["overall_fit"] is None and empty["coverage"] == 0
    sheet = client.get(quiz,headers=headers).json()
    assert sheet["items"] and all("correct" not in i for i in sheet["items"])
    assert client.post(quiz,headers=headers,json={"version":sheet["version"],"answers":{}}).status_code == 422
    keys = {q.key:(q.correct+1)%len(q.choices) for q in BANK}
    answers = {item["id"]:keys[item["id"]] for item in sheet["items"]}
    assert client.post(quiz,headers=headers,json={"version":sheet["version"],"answers":answers}).status_code == 201
    report = client.post("/api/assessments",headers=headers,json={"profile_id":profile_id,"self_scores":{"Python":60}}).json()
    assert report["coverage"] == 100 and not report["partial"]
    assert report["id"] != empty["id"]
    suggested = client.get("/api/route/suggestions",headers=headers).json()
    assert 0 < len(suggested) <= 3 and suggested[0]["priority"] >= suggested[-1]["priority"]
    enrolled = client.post(f"/api/route/enroll/{suggested[0]['resource_id']}",headers=headers).json()
    target = f"/api/progress/{enrolled['progress_id']}"
    assert client.patch(target,headers=other,json={"percent":100}).status_code == 404
    assert client.patch(target,headers=headers,json={"percent":100}).json()["completed"]
    assert client.get("/api/assessments/latest",headers=headers).json() == report
    assert not client.get("/api/progress",headers=other).json()


def test_catalog_revision_archive_and_restore():
    login = client.post("/api/auth/login",json={"email":"admin@skillgrow.pe","password":"Admin123!"})
    admin = {"Authorization":"Bearer " + login.json()["access_token"]}
    base = "/api/admin/catalog"
    skill = {"name":"Revisión de requisitos","category":"Blanda","aliases":["Clarificación"]}
    result = client.post(base+"/competencies",headers=admin,json=skill)
    assert result.status_code == 201
    target = base + "/competencies/" + str(result.json()["id"])
    assert client.put(target,headers=admin,json={**skill,"aliases":[]}).status_code == 200
    assert client.patch(target+"/status",headers=admin,json={"archived":True}).status_code == 200
    assert client.patch(target+"/status",headers=admin,json={"archived":False}).status_code == 200
    revisions = client.get(base+"/history",headers=admin).json()
    assert revisions[0]["version"] > result.json()["version"]
