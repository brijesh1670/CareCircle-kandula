import pytest

from tests.conftest import auth


def test_create_login_and_me(client, circle):
    me = client.get("/api/me", headers=auth(circle["tokens"]["elder"])).json()
    assert me["role"] == "elder" and me["elder_name"] == "Asha"


def test_wrong_passcode_is_generic_and_throttled(client, circle):
    body = {"profile_code": circle["code"], "passcode": "AAAA-BBBBBBBBBB"}
    assert [client.post("/api/auth/login", json=body).status_code for _ in range(3)] == [401, 401, 401]
    blocked = client.post("/api/auth/login", json={"profile_code": circle["code"], "passcode": circle["passcode"]})
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers  # even the right code is locked


def test_unknown_profile_looks_the_same_as_wrong_passcode(client):
    r = client.post("/api/auth/login", json={"profile_code": "NOSUCH", "passcode": "AAAA-BBBBBBBBBB"})
    assert r.status_code == 401 and "not correct" in r.json()["detail"]


def test_requires_token(client):
    assert client.get("/api/medications").status_code == 401
    assert client.get("/api/medications", headers=auth("garbage")).status_code == 401


def test_role_permissions(client, circle):
    t = circle["tokens"]
    med = {"name": "Metformin", "dosage": "500 mg", "times": ["08:00", "20:00"]}
    assert client.post("/api/medications", json=med, headers=auth(t["family"])).status_code == 403
    assert client.post("/api/medications", json=med, headers=auth(t["caregiver"])).status_code == 201
    assert client.get("/api/medications", headers=auth(t["family"])).status_code == 200
    invite = {"name": "X", "role": "family"}
    assert client.post("/api/members", json=invite, headers=auth(t["caregiver"])).status_code == 403
    assert client.get("/api/audit", headers=auth(t["caregiver"])).status_code == 403
    assert client.get("/api/audit", headers=auth(t["elder"])).status_code == 200


def test_validation_rejects_bad_input(client, circle):
    h = auth(circle["tokens"]["elder"])
    for bad in ({"name": "A", "times": ["25:00"]}, {"name": "A", "times": []},
                {"name": "", "times": ["08:00"]}, {"name": "A", "times": ["08:00"], "extra": 1},
                {"name": "A" * 101, "times": ["08:00"]}):
        assert client.post("/api/medications", json=bad, headers=h).status_code == 422


def test_medication_warnings_and_audit_trail(client, circle):
    h = auth(circle["tokens"]["caregiver"])
    client.post("/api/medications", json={"name": "Warfarin", "times": ["09:00"]}, headers=h)
    res = client.post("/api/medications", json={"name": "Aspirin", "times": ["09:00"]}, headers=h).json()
    assert res["warnings"]
    log = client.get("/api/audit", headers=auth(circle["tokens"]["elder"])).json()
    assert any(e["action"] == "medication_added" and e["actor"] == "Caregiver" for e in log)


def test_dose_flow(client, circle):
    h = auth(circle["tokens"]["elder"])
    client.post("/api/medications", json={"name": "Vit D", "times": ["00:00", "23:59"], "pills_remaining": 5}, headers=h)
    doses = client.get("/api/doses/today", headers=h).json()
    assert doses and doses[0]["status"] == "pending"
    assert client.post(f"/api/doses/{doses[0]['id']}/take", headers=h).json()["status"] == "taken"
    assert client.post(f"/api/doses/{doses[0]['id']}/dance", headers=h).status_code == 404
    fam = auth(circle["tokens"]["family"])
    assert client.post(f"/api/doses/{doses[0]['id']}/skip", headers=fam).status_code == 403
    assert client.get("/api/medications", headers=h).json()[0]["pills_remaining"] == 4


def test_tenant_isolation(client, circle):
    other = client.post("/api/profiles", json={"elder_name": "Bob"}).json()
    tok = client.post("/api/auth/login", json={"profile_code": other["profile_code"], "passcode": other["passcode"]}).json()["token"]
    mine = client.post("/api/medications", json={"name": "Mine", "times": ["08:00"]},
                       headers=auth(circle["tokens"]["elder"])).json()
    assert client.delete(f"/api/medications/{mine['id']}", headers=auth(tok)).status_code == 404
    assert client.get("/api/medications", headers=auth(tok)).json() == []


def test_rotating_passcode_signs_out_old_sessions(client, circle):
    elder, caregiver = auth(circle["tokens"]["elder"]), auth(circle["tokens"]["caregiver"])
    members = client.get("/api/members", headers=elder).json()
    cg = next(m for m in members if m["role"] == "caregiver")
    new = client.post(f"/api/members/{cg['id']}/rotate", headers=elder).json()
    assert client.get("/api/me", headers=caregiver).status_code == 401
    ok = client.post("/api/auth/login", json={"profile_code": circle["code"], "passcode": new["passcode"]})
    assert ok.status_code == 200


def test_removed_member_loses_access_and_owner_is_protected(client, circle):
    elder = auth(circle["tokens"]["elder"])
    members = client.get("/api/members", headers=elder).json()
    fam = next(m for m in members if m["role"] == "family")
    owner = next(m for m in members if m["role"] == "elder")
    assert client.delete(f"/api/members/{owner['id']}", headers=elder).status_code == 400
    assert client.delete(f"/api/members/{fam['id']}", headers=elder).status_code == 204
    assert client.get("/api/me", headers=auth(circle["tokens"]["family"])).status_code == 401


def test_family_cannot_see_member_emails(client, circle):
    rows = client.get("/api/members", headers=auth(circle["tokens"]["family"])).json()
    assert all(r["email"] is None for r in rows)


def test_appointments_use_profile_timezone(client, circle):
    h = auth(circle["tokens"]["caregiver"])
    res = client.post("/api/appointments", headers=h,
                      json={"title": "Cardiology", "starts_at_local": "2099-01-01T10:30"}).json()
    assert res["starts_at"] == "2099-01-01T05:00:00Z"  # Asia/Kolkata = UTC+5:30
    assert res["starts_at_local"] == "2099-01-01T10:30"


def test_security_headers_and_static_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/me").headers["cache-control"] == "no-store"
