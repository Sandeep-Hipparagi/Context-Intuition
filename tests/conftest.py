"""
Pytest configuration for VibeCore.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

import os
import sys
import tempfile
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))


@pytest.fixture(autouse=True)
def isolated_vibecore_home(tmp_path, monkeypatch):
    """
    Ensures all tests run against an isolated temporary directory for VIBECORE_HOME.
    """
    test_home = tmp_path / ".vibecore_test"
    test_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VIBECORE_HOME", str(test_home))
    monkeypatch.setenv("VIBECORE_BRIDGE_TOKEN", "test-secret-bridge-token-12345")
    return test_home
