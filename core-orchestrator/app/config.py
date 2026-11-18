"""
VibeCore System Configuration & Directory Management.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional


def get_vibecore_home() -> Path:
    """
    Returns the root directory for VibeCore runtime state, databases, and tokens.
    Defaults to ~/.vibecore or VIBECORE_HOME if specified in the environment.
    """
    custom_home = os.environ.get("VIBECORE_HOME")
    if custom_home:
        path = Path(custom_home).resolve()
    else:
        path = Path.home() / ".vibecore"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path(filename: str) -> Path:
    """
    Resolves the SQLite database path inside the VibeCore home directory.
    """
    return get_vibecore_home() / filename


def get_or_create_bridge_token() -> str:
    """
    Retrieves or generates an ephemeral authentication token for WebSocket bridges
    (Chrome Extension <-> Orchestrator and Dashboard <-> Orchestrator).
    """
    token_file = get_vibecore_home() / "bridge.token"
    env_token = os.environ.get("VIBECORE_BRIDGE_TOKEN")

    if env_token:
        token = env_token.strip()
    elif token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
    else:
        token = secrets.token_hex(24)
        token_file.write_text(token, encoding="utf-8")
        try:
            # Restrict permissions on Unix-like environments if possible
            token_file.chmod(0o600)
        except Exception:
            pass

    return token


class Settings:
    VIBECORE_HOME: Path = get_vibecore_home()
    BRIDGE_TOKEN: str = get_or_create_bridge_token()
    LOCAL_LLM_URL: str = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434")
    DEFAULT_CONFIDENCE_THRESHOLD: float = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.80"))
    ORCHESTRATOR_HOST: str = os.environ.get("ORCHESTRATOR_HOST", "127.0.0.1")
    ORCHESTRATOR_PORT: int = int(os.environ.get("ORCHESTRATOR_PORT", "8000"))


settings = Settings()
