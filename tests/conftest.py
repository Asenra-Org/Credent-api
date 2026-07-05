import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    """
    Returns a FastAPI TestClient instance for testing routes.
    """
    return TestClient(app)
