"""
VibeCore Policy Loader - Declarative Policy-as-Code with Hot-Reload.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Loads and validates policy.yaml, supports hot-reloading on file modification.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from app.schemas import TierLevel
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger("PolicyLoader")


class ConditionModel(BaseModel):
    """Base model for conditional tier overrides."""
    type: str


class RecipientDomainCondition(ConditionModel):
    """Condition based on recipient email domain."""
    type: str = "recipient_domain"
    allowlist: list[str] = Field(default_factory=list)
    allowlist_tier: TierLevel = TierLevel.TIER_2_COMMIT


class FinancialThresholdCondition(ConditionModel):
    """Condition based on financial amount thresholds."""
    type: str = "financial_threshold"
    field: str = Field(description="Payload field to check (e.g., 'amount', 'total_price')")
    threshold: float = Field(ge=0.0)
    above_threshold_tier: TierLevel = TierLevel.TIER_2_COMMIT
    risk_boost: float = Field(default=0.0, ge=0.0, le=1.0)


class AllowedRootsCondition(ConditionModel):
    """Condition for filesystem sandbox roots."""
    type: str = "allowed_roots"
    paths: list[str] = Field(default_factory=list, description="Allowed subdirectories relative to VIBECORE_HOME")
    deny_traversal: bool = True


ConditionModelUnion = RecipientDomainCondition | FinancialThresholdCondition | AllowedRootsCondition


class ToolPolicyModel(BaseModel):
    """Policy configuration for a single tool."""
    tier: TierLevel
    description: str = ""
    conditions: list[ConditionModelUnion] = Field(default_factory=list)

    @field_validator("conditions", mode="before")
    @classmethod
    def parse_conditions(cls, v: Any) -> list[ConditionModelUnion]:
        if not v:
            return []
        result: list[ConditionModelUnion] = []
        for item in v:
            if isinstance(item, dict):
                condition_type = item.get("type")
                if condition_type == "recipient_domain":
                    result.append(RecipientDomainCondition(**item))
                elif condition_type == "financial_threshold":
                    result.append(FinancialThresholdCondition(**item))
                elif condition_type == "allowed_roots":
                    result.append(AllowedRootsCondition(**item))
                else:
                    logger.warning(f"Unknown condition type: {condition_type}")
            elif isinstance(item, (RecipientDomainCondition, FinancialThresholdCondition, AllowedRootsCondition)):
                result.append(item)
        return result


class PolicyConfig(BaseModel):
    """Complete policy configuration."""
    default_tier: TierLevel = TierLevel.TIER_2_COMMIT
    hot_reload: bool = True
    tools: dict[str, ToolPolicyModel] = Field(default_factory=dict)
    domain_allowlists: dict[str, list[str]] = Field(default_factory=dict)
    financial_thresholds: dict[str, float] = Field(default_factory=dict)
    sandbox_roots: list[str] = Field(default_factory=list)

    def get_tool_policy(self, tool_name: str) -> ToolPolicyModel:
        """Get policy for a tool, falling back to default."""
        return self.tools.get(tool_name, ToolPolicyModel(
            tier=self.default_tier,
            description=f"Default policy for {tool_name}"
        ))

    def evaluate_conditions(
        self,
        tool_name: str,
        payload: dict[str, Any],
        base_tier: TierLevel,
        base_risk: float
    ) -> tuple[TierLevel, float, list[str]]:
        """Evaluate conditional overrides for a tool."""
        policy = self.get_tool_policy(tool_name)
        effective_tier = policy.tier
        effective_risk = base_risk
        risk_factors: list[str] = []

        for condition in policy.conditions:
            if isinstance(condition, RecipientDomainCondition):
                recipient = payload.get("recipient") or payload.get("to") or payload.get("chat_name") or ""
                if "@" in str(recipient):
                    domain = str(recipient).split("@")[-1].lower()
                    if domain in condition.allowlist:
                        # Downgrade tier if recipient is in allowlist
                        if condition.allowlist_tier.value < effective_tier.value:
                            effective_tier = condition.allowlist_tier
                            risk_factors.append(f"Recipient domain '{domain}' in allowlist -> tier {condition.allowlist_tier.value}")
                    else:
                        risk_factors.append(f"External domain recipient: {domain}")

            elif isinstance(condition, FinancialThresholdCondition):
                value = payload.get(condition.field)
                if value is not None:
                    try:
                        amount = float(value)
                        if amount > condition.threshold:
                            effective_risk = min(1.0, effective_risk + condition.risk_boost)
                            risk_factors.append(f"Financial threshold exceeded: ${amount:.2f} > ${condition.threshold:.2f} (+{condition.risk_boost} risk)")
                            if condition.above_threshold_tier.value > effective_tier.value:
                                effective_tier = condition.above_threshold_tier
                    except (ValueError, TypeError):
                        pass

            elif isinstance(condition, AllowedRootsCondition):
                # This is handled at execution time in dispatcher
                risk_factors.append("Filesystem operation - sandbox roots enforced at execution")

        return effective_tier, round(effective_risk, 2), risk_factors


class PolicyLoader:
    """
    Thread-safe policy loader with hot-reload support.
    Checks file mtime before each evaluation to pick up changes without restart.
    """

    def __init__(self, policy_path: str | None = None):
        resolved_path = policy_path or os.environ.get("VIBECORE_POLICY_PATH")
        if not resolved_path:
            # Default to policy-enclave/policy.yaml relative to project root
            root = Path(__file__).resolve().parent.parent
            resolved_path = str(root / "policy-enclave" / "policy.yaml")
        self.policy_path: str = resolved_path

        self._policy: PolicyConfig | None = None
        self._last_mtime: float = 0.0
        self._lock = threading.RLock()

    def load(self, force: bool = False) -> PolicyConfig:
        """Load policy from YAML file, with hot-reload support."""
        with self._lock:
            path = Path(self.policy_path)
            if not path.exists():
                logger.warning(f"Policy file not found at {self.policy_path}, using defaults")
                return PolicyConfig()

            mtime = path.stat().st_mtime
            if not force and self._policy is not None and mtime <= self._last_mtime:
                return self._policy

            try:
                with open(path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}

                # Convert tier strings to TierLevel enum
                if "default_tier" in raw and isinstance(raw["default_tier"], str):
                    raw["default_tier"] = TierLevel(raw["default_tier"])

                if "tools" in raw:
                    for tool_config in raw["tools"].values():
                        if "tier" in tool_config and isinstance(tool_config["tier"], str):
                            tool_config["tier"] = TierLevel(tool_config["tier"])
                        if "conditions" in tool_config:
                            for cond in tool_config["conditions"]:
                                if "allowlist_tier" in cond and isinstance(cond["allowlist_tier"], str):
                                    cond["allowlist_tier"] = TierLevel(cond["allowlist_tier"])
                                if "above_threshold_tier" in cond and isinstance(cond["above_threshold_tier"], str):
                                    cond["above_threshold_tier"] = TierLevel(cond["above_threshold_tier"])

                self._policy = PolicyConfig(**raw)
                self._last_mtime = mtime
                loaded_ts = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
                logger.info(f"Policy loaded from {self.policy_path} (mtime: {loaded_ts})")
                return self._policy

            except (OSError, yaml.YAMLError, ValidationError, ValueError, TypeError) as e:
                logger.error(f"Failed to load policy from {self.policy_path}: {e}")
                if self._policy is None:
                    return PolicyConfig()
                return self._policy

    def get_policy(self) -> PolicyConfig:
        """Get current policy (with hot-reload check)."""
        return self.load()

    def get_tool_tier(self, tool_name: str) -> TierLevel:
        """Get effective tier for a tool (with hot-reload)."""
        return self.load().get_tool_policy(tool_name).tier

    def get_tool_policy(self, tool_name: str) -> ToolPolicyModel:
        """Get full tool policy model for a tool (with hot-reload)."""
        return self.load().get_tool_policy(tool_name)

    def evaluate(
        self,
        tool_name: str,
        payload: dict[str, Any],
        base_risk: float = 0.0
    ) -> tuple[TierLevel, float, list[str]]:
        """Evaluate tool with conditions (with hot-reload)."""
        policy = self.load()
        base_tier = policy.get_tool_policy(tool_name).tier
        return policy.evaluate_conditions(tool_name, payload, base_tier, base_risk)


# Global loader instance
_global_loader: PolicyLoader | None = None


def get_policy_loader() -> PolicyLoader:
    global _global_loader
    if _global_loader is None:
        _global_loader = PolicyLoader()
    return _global_loader


def reload_policy() -> PolicyConfig:
    """Force reload policy from disk."""
    loader = get_policy_loader()
    return loader.load(force=True)
