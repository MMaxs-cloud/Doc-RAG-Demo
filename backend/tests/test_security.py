from fastapi.testclient import TestClient

from app.main import create_app


def test_production_auth_and_download_headers(settings):
    settings = settings.model_copy(update={"environment": "production", "user": "test", "password": "fixture"})
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").status_code == 401
        assert client.get("/api/v1/health", auth=("test", "wrong")).status_code == 401
        assert client.get("/api/v1/health", auth=("test", "fixture")).status_code == 200
        response = client.post("/api/v1/documents/upload", auth=("test", "fixture"), files={
            "files": ("../example.html", b"<html><p>Information</p><script>alert(1)</script></html>", "text/html")
        })
        result = response.json()["items"][0]["document"]
        assert result["filename"] == "example.html"
        original = client.get(f"/api/v1/documents/{result['id']}/file", auth=("test", "fixture"))
        assert original.headers["content-type"] == "application/octet-stream"
        assert original.headers["content-disposition"].startswith("attachment")
        assert original.headers["x-content-type-options"] == "nosniff"
