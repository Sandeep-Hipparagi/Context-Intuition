"""
VibeCore Capability Tokens - Cryptographic HMAC-SHA256 Authorization.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Short-lived, single-use capability tokens minted by PolicyEnclave for ToolDispatcher verification.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import get_vibecore_home

logger = logging.getLogger("Capabilities")

# Token format: base64url(payload).base64url(signature)
# Payload: {"action_id": "...", "tool_name": "...", "payload_hash": "...", "expires_at": ..., "nonce": "..."}


@dataclass
class CapabilityToken:
    """Parsed capability token."""
    action_id: str
    tool_name: str
    payload_hash: str
    expires_at: int
    nonce: str

    def is_expired(self) -> bool:
        return int(time.time()) >= self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "tool_name": self.tool_name,
            "payload_hash": self.payload_hash,
            "expires_at": self.expires_at,
            "nonce": self.nonce,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityToken":
        return cls(
            action_id=data["action_id"],
            tool_name=data["tool_name"],
            payload_hash=data["payload_hash"],
            expires_at=data["expires_at"],
            nonce=data["nonce"],
        )


class CapabilityManager:
    """
    Manages HMAC signing secret and token minting/verification.
    Secret stored at ~/.vibecore/profiles/<profile>/capability.key (0600).
    """

    def __init__(self, profile: str = "default"):
        self.profile = profile
        self._secret: Optional[bytes] = None
        self._used_nonces: set[str] = set()  # In-memory replay protection

    def _get_secret_path(self) -> Path:
        """Get path to capability secret for current profile."""
        vibecore_home = get_vibecore_home()
        profile_dir = vibecore_home / "profiles" / self.profile
        profile_dir.mkdir(parents=True, exist_ok=True)
        return profile_dir / "capability.key"

    def _load_or_create_secret(self) -> bytes:
        """Load existing secret or generate new one."""
        if self._secret is not None:
            return self._secret

        secret_path = self._get_secret_path()
        if secret_path.exists():
            secret = secret_path.read_bytes()
            if len(secret) != 32:
                logger.warning(f"Invalid secret length ({len(secret)} bytes), regenerating")
                secret = secrets.token_bytes(32)
                secret_path.write_bytes(secret)
                try:
                    secret_path.chmod(0o600)
                except Exception:
                    pass
        else:
            secret = secrets.token_bytes(32)
            secret_path.write_bytes(secret)
            try:
                secret_path.chmod(0o600)
            except Exception:
                pass
            logger.info(f"Generated new capability secret at {secret_path}")

        self._secret = secret
        return secret

    def _canonical_payload_hash(self, payload: Dict[str, Any]) -> str:
        """Compute canonical SHA-256 hash of payload (sorted keys, no whitespace)."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _encode_payload(self, token: CapabilityToken) -> str:
        """Encode token payload as base64url JSON."""
        data = json.dumps(token.to_dict(), separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def _decode_payload(self, encoded: str) -> Dict[str, Any]:
        """Decode base64url JSON payload."""
        # Add padding if needed
        padding = "=" * (-len(encoded) % 4)
        data = base64.urlsafe_b64decode(encoded + padding)
        return json.loads(data.decode("utf-8"))

    def _sign(self, payload_b64: str) -> str:
        """Create HMAC-SHA256 signature."""
        secret = self._load_or_create_secret()
        sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")

    def _verify_signature(self, payload_b64: str, sig_b64: str) -> bool:
        """Verify HMAC-SHA256 signature using constant-time comparison."""
        secret = self._load_or_create_secret()
        expected = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        expected_b64 = base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
        return hmac.compare_digest(expected_b64, sig_b64)

    def mint(
        self,
        action_id: str,
        tool_name: str,
        payload: Dict[str, Any],
        expires_in: int = 60,
    ) -> str:
        """
        Mint a new capability token.
        Returns: base64url(payload).base64url(signature)
        """
        payload_hash = self._canonical_payload_hash(payload)
        nonce = secrets.token_urlsafe(16)
        expires_at = int(time.time()) + expires_in

        token = CapabilityToken(
            action_id=action_id,
            tool_name=tool_name,
            payload_hash=payload_hash,
            expires_at=expires_at,
            nonce=nonce,
        )

        payload_b64 = self._encode_payload(token)
        signature = self._sign(payload_b64)
        return f"{payload_b64}.{signature}"

    def verify(
        self,
        token_str: str,
        tool_name: str,
        payload: Dict[str, Any],
    ) -> CapabilityToken:
        """
        Verify a capability token.
        Raises CapabilityVerificationError on any failure.
        """
        if not token_str or "." not in token_str:
            raise CapabilityVerificationError("Invalid token format")

        payload_b64, sig_b64 = token_str.split(".", 1)

        # Verify signature
        if not self._verify_signature(payload_b64, sig_b64):
            raise CapabilityVerificationError("Invalid signature")

        # Decode payload
        try:
            data = self._decode_payload(payload_b64)
            token = CapabilityToken.from_dict(data)
        except Exception as e:
            raise CapabilityVerificationError(f"Invalid payload: {e}")

        # Check expiration
        if token.is_expired():
            raise CapabilityVerificationError("Token expired")

        # Check tool_name matches
        if token.tool_name != tool_name:
            raise CapabilityVerificationError(f"Token tool mismatch: {token.tool_name} != {tool_name}")

        # Check payload hash matches (prevents post-approval tampering)
        expected_hash = self._canonical_payload_hash(payload)
        if not hmac.compare_digest(token.payload_hash, expected_hash):
            raise CapabilityVerificationError("Payload hash mismatch - tampering detected")

        # Check nonce replay (single-use)
        if token.nonce in self._used_nonces:
            raise CapabilityVerificationError("Token nonce already used (replay attack)")
        self._used_nonces.add(token.nonce)

        return token

    def cleanup_expired_nonces(self, max_age_seconds: int = 3600) -> int:
        """Clean up old nonces to prevent memory growth. Returns count removed."""
        # In production, would use timestamp in nonce or separate store
        # For now, simple approach: clear if too many
        if len(self._used_nonces) > 10000:
            self._used_nonces.clear()
            return 10000
        return 0


# Global manager per profile
_managers: Dict[str, CapabilityManager] = {}


def get_capability_manager(profile: str = "default") -> CapabilityManager:
    global _managers
    if profile not in _managers:
        _managers[profile] = CapabilityManager(profile)
    return _managers[profile]


def mint_capability_token(
    action_id: str,
    tool_name: str,
    payload: Dict[str, Any],
    expires_in: int = 60,
    profile: str = "default",
) -> str:
    """Mint a capability token using the profile's manager."""
    manager = get_capability_manager(profile)
    return manager.mint(action_id, tool_name, payload, expires_in)


def verify_capability_token(
    token_str: str,
    tool_name: str,
    payload: Dict[str, Any],
    profile: str = "default",
) -> CapabilityToken:
    """Verify a capability token using the profile's manager."""
    manager = get_capability_manager(profile)
    return manager.verify(token_str, tool_name, payload)


class CapabilityVerificationError(Exception):
    """Raised when capability token verification fails."""
    pass