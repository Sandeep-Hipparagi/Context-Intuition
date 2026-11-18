"""
Unit tests for VibeCore configuration and directory management.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

from pathlib import Path
from app.config import get_vibecore_home, get_db_path, get_or_create_bridge_token


def test_vibecore_home_isolated(isolated_vibecore_home):
    home = get_vibecore_home()
    assert home == isolated_vibecore_home
    assert home.exists()


def test_db_path_resolution(isolated_vibecore_home):
    db_file = get_db_path("test.db")
    assert db_file.parent == isolated_vibecore_home
    assert db_file.name == "test.db"


def test_bridge_token_resolution():
    token = get_or_create_bridge_token()
    assert token == "test-secret-bridge-token-12345"


def test_api_health_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["version"] == "0.1.0"
    assert "scheduler_running" in data
