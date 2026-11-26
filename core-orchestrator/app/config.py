"""
VibeCore System Configuration & Directory Management.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Optional


# Profile name validation: alphanumeric, underscore, hyphen, 1-32 chars
PROFILE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
DEFAULT_PROFILE = "default"


def _validate_profile_name(profile: str) -> str:
    """Validate profile name to prevent path traversal."""
    if not PROFILE_NAME_PATTERN.match(profile):
        raise ValueError(
            f"Invalid profile name '{profile}'. "
            f"Must match ^[a-zA-Z0-9_-]{{1,32}}$"
        )
    return profile


def get_profile_name() -> str:
    """Get current profile name from environment or default."""
    profile = os.environ.get("VIBECORE_PROFILE", DEFAULT_PROFILE)
    return _validate_profile_name(profile)


def get_vibecore_home(profile: Optional[str] = None) -> Path:
    """
    Returns the root directory for VibeCore runtime state, databases, and tokens.
    Defaults to ~/.vibecore/profiles/<profile> or VIBECORE_HOME if specified.
    When VIBECORE_HOME is set, profile subdirectory is still appended for isolation.
    """
    profile = profile or get_profile_name()

    custom_home = os.environ.get("VIBECORE_HOME")
    if custom_home:
        path = Path(custom_home).resolve() / "profiles" / profile
    else:
        path = Path.home() / ".vibecore" / "profiles" / profile

    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path(filename: str, profile: Optional[str] = None) -> Path:
    """
    Resolves the SQLite database path inside the VibeCore home directory for a profile.
    """
    return get_vibecore_home(profile) / filename


def get_or_create_bridge_token(profile: Optional[str] = None) -> str:
    """
    Retrieves or generates an ephemeral authentication token for WebSocket bridges
    (Chrome Extension <-> Orchestrator and Dashboard <-> Orchestrator).
    Scoped to profile.
    """
    profile = profile or get_profile_name()
    token_file = get_vibecore_home(profile) / "bridge.token"
    env_token = os.environ.get("VIBECORE_BRIDGE_TOKEN")

    if env_token:
        token = env_token.strip()
    elif token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
    else:
        token = secrets.token_hex(24)
        token_file.write_text(token, encoding="utf-8")
        try:
            token_file.chmod(0o600)
        except Exception:
            pass

    return token


def get_or_create_capability_secret(profile: Optional[str] = None) -> Path:
    """
    Returns path to capability signing secret for a profile.
    Secret is managed by CapabilityManager, this just ensures directory exists.
    """
    profile = profile or get_profile_name()
    secret_dir = get_vibecore_home(profile)
    secret_dir.mkdir(parents=True, exist_ok=True)
    return secret_dir / "capability.key"


class Settings:
    VIBECORE_HOME: Path = get_vibecore_home()
    BRIDGE_TOKEN: str = get_or_create_bridge_token()
    LOCAL_LLM_URL: str = os.environ.get("LOCAL_LLM_URL", "http://localhost:11434")
    DEFAULT_CONFIDENCE_THRESHOLD: float = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.80"))
    ORCHESTRATOR_HOST: str = os.environ.get("ORCHESTRATOR_HOST", "127.0.0.1")
    ORCHESTRATOR_PORT: int = int(os.environ.get("ORCHESTRATOR_PORT", "8000"))

    # Wave 1: Production LLM Configuration
    OLLAMA_URL: str = os.environ.get("VIBECORE_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
    LOCAL_OPENAI_URL: str = os.environ.get("VIBECORE_LOCAL_OPENAI_URL", "")
    CLOUD_API_URL: str = os.environ.get("VIBECORE_CLOUD_API_URL", "")
    CLOUD_API_KEY: str = os.environ.get("VIBECORE_CLOUD_API_KEY", "")
    FAST_MODEL: str = os.environ.get("VIBECORE_FAST_MODEL", "qwen2.5:1.5b")
    REASONING_MODEL: str = os.environ.get("VIBECORE_REASONING_MODEL", "llama3.1:8b")
    MOCK_LLM: bool = os.environ.get("VIBECORE_MOCK_LLM", "0") == "1"

    # Wave 2: Multi-Profile Support
    @property
    def PROFILE(self) -> str:
        return get_profile_name()


settings = Settings()