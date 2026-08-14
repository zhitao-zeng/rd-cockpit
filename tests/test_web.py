from pathlib import Path

from fastapi.testclient import TestClient

from rd_cockpit.web import create_web_app


def _fixture(tmp_path: Path, projects: str = "projects: {}\n") -> tuple[Path, Path]:
    home = tmp_path / "cockpit"
    (home / "config").mkdir(parents=True)
    (home / "config" / "projects.yaml").write_text(projects, encoding="utf-8")
    dist = home / "frontend" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<main>cockpit</main>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export {};\n", encoding="utf-8")
    return home, dist


def test_production_web_serves_spa_and_prefixed_api(tmp_path: Path) -> None:
    home, dist = _fixture(tmp_path)

    client = TestClient(create_web_app(home, dist))
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/projects").status_code == 404
    assert client.get("/api/docs").status_code == 404
    assert "cockpit" in client.get("/projects/demo").text
    assert client.get("/assets/app.js").status_code == 200


def test_production_api_requires_bearer_token_when_configured(
    tmp_path: Path, monkeypatch,
) -> None:
    home, dist = _fixture(tmp_path, """projects:
  demo:
    name: Demo
    repo_path: /mnt/private/user/demo
    lifecycle_status: active
""")
    monkeypatch.setenv("RD_API_TOKEN", "fixture-token")
    client = TestClient(create_web_app(home, dist))

    assert client.get("/api/health").status_code == 401
    assert client.get("/api/auth/status").json() == {
        "required": True, "authenticated": False,
    }
    headers = {"Authorization": "Bearer fixture-token"}
    assert client.get("/api/health", headers=headers).json() == {"ok": True}
    assert client.get("/api/auth/status", headers=headers).json()["authenticated"] is True
    assert client.get("/api/projects", headers=headers).status_code == 404
    projects = client.get("/api/simple/projects", headers=headers).json()
    assert projects == {
        "demo": {"project_id": "demo", "name": "Demo", "lifecycle_status": "active"},
    }
    assert "/mnt/" not in str(projects)


def test_production_json_redacts_paths_private_hosts_and_identifiers(tmp_path: Path) -> None:
    home, dist = _fixture(tmp_path)
    from rd_cockpit.api import create_app

    api = create_app(home, safe_mode=True)
    private_host = ".".join(("172", "20", "10", "5"))
    private_home = "/" + "home/example/.agent/log"

    @api.get("/simple/privacy-fixture")
    def privacy_fixture():
        return {
            "text": f"repo:/srv/example/repo called {private_host} from {private_home}",
            "machine": "gpu-server-02", "session_id": "codex-secret-session",
        }

    client = TestClient(api)
    body = client.get("/simple/privacy-fixture").json()
    encoded = str(body)
    assert "/mnt/" not in encoded
    assert ("/" + "home/") not in encoded
    assert private_host not in encoded
    assert "codex-secret-session" not in encoded
    assert body["machine"] == "<remote>"


def test_production_web_compresses_large_api_responses(tmp_path: Path) -> None:
    projects = {f"project_{index}": {"name": "研究项目" * 20} for index in range(40)}
    import yaml

    home, dist = _fixture(tmp_path, yaml.safe_dump({"projects": projects}, allow_unicode=True))
    client = TestClient(create_web_app(home, dist))
    response = client.get("/api/simple/projects", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"


def test_production_uses_compact_paginated_development_endpoints(
    tmp_path: Path, monkeypatch,
) -> None:
    home, dist = _fixture(tmp_path, """projects:
  demo:
    name: Demo
    repo_path: /example/demo
    lifecycle_status: active
""")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "2026-08-01.md").write_text("""# 日报 2026-08-01

## 核心进展
### Demo
#### 完成本地评测
- **做了什么**：运行本地回归。
- **结果**：18 tests passed。

## 昨日计划闭环
- Demo 本地回归：completed
""", encoding="utf-8")
    monkeypatch.setenv("RD_DAILY_REPORT_DIR", str(reports))
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(home / "config" / "projects.yaml"))
    from rd_cockpit.ledger import Ledger
    Ledger(home / ".rd-cockpit" / "events.sqlite").close()
    client = TestClient(create_web_app(home, dist))

    assert client.get("/api/simple/development", params={"target_date": "2026-08-01"}).status_code == 404
    summary = client.get("/api/simple/development-summary", params={"target_date": "2026-08-01"})
    assert summary.status_code == 200
    assert summary.headers["x-rd-privacy-safe"] == "1"
    assert summary.json()["counts"]["nodes"] == 1
    detail = client.get(
        "/api/simple/development-project/demo", params={"target_date": "2026-08-01"},
    ).json()
    assert detail["timeline_total"] == 1
    assert len(detail["storyline"]) == 1
    timeline = client.get(
        "/api/simple/development-timeline",
        params={"target_date": "2026-08-01", "project": "demo", "limit": 1},
    ).json()
    assert timeline["total"] == 1 and timeline["has_more"] is False


def test_semantic_feedback_is_private_append_only_api(tmp_path: Path, monkeypatch) -> None:
    home, dist = _fixture(tmp_path, """projects:
  demo:
    name: Demo
""")
    monkeypatch.setenv("RD_PROJECTS_CONFIG", str(home / "config" / "projects.yaml"))
    client = TestClient(create_web_app(home, dist))
    value = {
        "view": "storyline", "item_id": "storyline:demo", "project_id": "demo",
        "rating": "incorrect", "text": "摘要不准确", "source_dates": ["2026-08-01"],
    }

    created = client.post("/api/simple/semantic-feedback", json=value)

    assert created.status_code == 200
    listed = client.get(
        "/api/simple/semantic-feedback", params={"view": "storyline", "project": "demo"},
    ).json()
    assert listed["count"] == 1
    assert listed["items"][0]["rating"] == "incorrect"
