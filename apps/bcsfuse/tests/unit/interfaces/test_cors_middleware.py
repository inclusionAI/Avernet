from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.interfaces.api.cors_middleware import RegexCORSMiddleware


def test_legacy_star_origin_pattern_allows_any_origin() -> None:
    """A bare star must keep its historical allow-any-origin meaning."""
    app = FastAPI()
    app.add_middleware(
        RegexCORSMiddleware,
        allow_origins=[],
        allow_origin_regex=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    response = TestClient(app).get(
        "/health",
        headers={"Origin": "https://example.test"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://example.test"
