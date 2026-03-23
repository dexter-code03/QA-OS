def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "uptime_seconds" in data


def test_auth_token(client):
    r = client.get("/api/auth/token")
    assert r.status_code == 200
    assert "token" in r.json()


def test_projects_crud(client):
    # Create
    r = client.post("/api/projects", json={"name": "Test Project"})
    assert r.status_code == 200
    pid = r.json()["id"]

    # List
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert any(p["id"] == pid for p in r.json())

    # Delete
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200
