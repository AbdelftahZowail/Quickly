from fastapi.testclient import TestClient
from app.main import app


def test_cors_headers_present():
    import asyncio
    from app.database import init_db
    asyncio.run(init_db())
    client = TestClient(app)
    """A simple OPTIONS request from the frontend origin should be accepted.

    This verifies that the CORSMiddleware is configured and that the
    Access-Control-Allow-Origin header is returned for known origins.
    """
    # FastAPI's TestClient automatically sends an Origin header if you pass
    # one, but we'll set it explicitly for clarity.
    response = client.options(
        "/api/status",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
