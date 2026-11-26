"""
Unit tests for VibeCore configuration and directory management.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

from pathlib import Path
from app.config import get_vibecore_home, get_db_path, get_or_create_bridge_token, get_profile_name


def test_vibecore_home_isolated(isolated_vibecore_home):
    # With VIBECORE_HOME set, profile subdirectory is appended
    expected_home = isolated_vibecore_home / "profiles" / "default"
    home = get_vibecore_home()
    assert home == expected_home
    assert home.exists()


def test_db_path_resolution(isolated_vibecore_home):
    expected_home = isolated_vibecore_home / "profiles" / "default"
    db_file = get_db_path("test.db")
    assert db_file.parent == expected_home
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
    assert data["version"] == "0.3.0"
    assert "scheduler_running" in data
    assert "profile" in data


def test_profile_name_validation():
    assert get_profile_name() == "default"
    import os
    os.environ["VIBECORE_PROFILE"] = "work"
    try:
        assert get_profile_name() == "work"
    finally:
        os.environ.pop("VIBECORE_PROFILE", None)
