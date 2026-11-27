"""
Pytest configuration for VibeCore.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

# Ensure module-level imports during test collection never touch the user's real ~/.vibecore
_SESSION_HOME = Path(tempfile.mkdtemp(prefix="vibecore_pytest_session_"))
os.environ.setdefault("VIBECORE_HOME", str(_SESSION_HOME))
os.environ.setdefault("VIBECORE_BRIDGE_TOKEN", "test-secret-bridge-token-12345")
atexit.register(lambda: shutil.rmtree(_SESSION_HOME, ignore_errors=True))


@pytest.fixture(autouse=True)
def isolated_vibecore_home(tmp_path, monkeypatch):
    """
    Ensures all tests run against an isolated temporary directory for VIBECORE_HOME
    and rebinds shared orchestrator singletons to per-test SQLite databases.
    """
    test_home = tmp_path / ".vibecore_test"
    test_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VIBECORE_HOME", str(test_home))
    monkeypatch.setenv("VIBECORE_BRIDGE_TOKEN", "test-secret-bridge-token-12345")
    monkeypatch.delenv("VIBECORE_PROFILE", raising=False)

    from app.config import get_db_path

    if "app.main" in sys.modules:
        main_mod = sys.modules["app.main"]
        main_mod.context_graph.db_path = str(get_db_path("context_ledger.db"))
        main_mod.context_graph._init_db()
        main_mod.edit_delta_engine.db_path = str(get_db_path("behavioral_rules.db"))
        main_mod.edit_delta_engine._init_db()
        main_mod.policy_enclave.queue.db_path = str(get_db_path("enclave_approvals.db"))
        main_mod.policy_enclave.queue._pending_futures.clear()
        main_mod.policy_enclave.queue._init_db()
        main_mod.tool_dispatcher._handlers.clear()
        main_mod.tool_dispatcher._register_default_handlers()
        main_mod.frame_deduplicator.clear()

    if "enclave" in sys.modules:
        enclave_mod = sys.modules["enclave"]
        enclave_mod._global_enclave.queue.db_path = str(get_db_path("enclave_approvals.db"))
        enclave_mod._global_enclave.queue._pending_futures.clear()
        enclave_mod._global_enclave.queue._init_db()

    return test_home
