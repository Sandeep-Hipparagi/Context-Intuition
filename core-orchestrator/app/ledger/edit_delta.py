"""
Edit-Delta Learning Engine.
Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)

Whenever the user modifies an agent's drafted communication or staging payload
before approving it, this engine computes the semantic diff and synthesizes
an implicit BehavioralRule to continuously adapt to user preferences.
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional
from uuid import uuid4

import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "core-orchestrator"))
sys.path.insert(0, str(ROOT_DIR / "policy-enclave"))
sys.path.insert(0, str(ROOT_DIR / "mcp-tools"))

from app.config import get_db_path
from app.schemas import (
    ActionEnvelope,
    BehavioralCategory,
    BehavioralRule,
)

logger = logging.getLogger("EditDeltaEngine")


class EditDeltaEngine:
    """
    Computes semantic differences between agent proposals and final user edits,
    inducing reusable BehavioralRules without expanding prompt token windows.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(get_db_path("behavioral_rules.db"))
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS behavioral_rules (
                    rule_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    trigger_pattern TEXT NOT NULL,
                    action_directive TEXT NOT NULL,
                    original_draft_sample TEXT,
                    user_revised_sample TEXT,
                    inferred_delta_rationale TEXT NOT NULL,
                    weight REAL NOT NULL,
                    reinforcement_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    last_triggered_at TEXT
                )
            """)
            conn.commit()

    async def learn_from_edit(
        self,
        original_envelope: ActionEnvelope,
        revised_payload: Dict[str, Any],
    ) -> Optional[BehavioralRule]:
        """
        Analyzes the delta between original payload and revised payload.
        """
        original_payload = original_envelope.payload
        tool_name = original_envelope.tool_name

        # Detect text diff in common fields like body, message, text, notes
        text_keys = ["body", "message", "text", "content", "summary"]
        orig_text: Optional[str] = None
        rev_text: Optional[str] = None

        for k in text_keys:
            if k in original_payload and k in revised_payload:
                orig_text = str(original_payload[k])
                rev_text = str(revised_payload[k])
                break

        if not orig_text or not rev_text or orig_text.strip() == rev_text.strip():
            logger.info("No significant text delta detected in revised payload.")
            return None

        # Compute line/word diff
        diff_lines = list(difflib.unified_diff(
            orig_text.splitlines(keepends=True),
            rev_text.splitlines(keepends=True),
            fromfile="agent_draft",
            tofile="user_final",
        ))

        # Semantic heuristic extraction (local rule induction)
        category = BehavioralCategory.COMMUNICATION_TONE
        rationale_parts = []
        action_directives = []

        orig_len = len(orig_text.split())
        rev_len = len(rev_text.split())

        # 1. Brevity check
        if rev_len < orig_len * 0.7:
            rationale_parts.append(f"User compressed length by {int((1 - rev_len/orig_len)*100)}%")
            action_directives.append("Favor extreme brevity; strip conversational filler and pleasantries.")

        # 2. Formality / Salutation check
        pleasantries = ["hope you are well", "hope this email finds you well", "best regards", "cheers", "sincerely"]
        orig_has_pleasantry = any(p in orig_text.lower() for p in pleasantries)
        rev_has_pleasantry = any(p in rev_text.lower() for p in pleasantries)
        if orig_has_pleasantry and not rev_has_pleasantry:
            rationale_parts.append("User deliberately deleted formal opening/closing pleasantries")
            action_directives.append("Omit traditional email greetings; begin immediately with actionable content.")

        # 3. Tone / Formatting (Bullet points vs Paragraphs)
        if ("\n-" in rev_text or "\n*" in rev_text) and not ("\n-" in orig_text or "\n*" in orig_text):
            rationale_parts.append("User reformatted dense prose into structured bullet points")
            action_directives.append("Format multi-item updates as bulleted lists rather than running prose.")

        # Counterparty condition
        recipient = original_payload.get("recipient") or original_payload.get("to") or "general_contacts"
        trigger = f"tool == '{tool_name}' and target == '{recipient}'"

        inferred_rationale = "; ".join(rationale_parts) or "User refined phrasing for tone and clarity"
        action_directive = " ".join(action_directives) or "Match exact concise syntax demonstrated in user revision."

        rule = BehavioralRule(
            rule_id=str(uuid4()),
            category=category,
            trigger_pattern=trigger,
            action_directive=action_directive,
            original_draft_sample=orig_text[:500],
            user_revised_sample=rev_text[:500],
            inferred_delta_rationale=inferred_rationale,
            weight=1.5,
            reinforcement_count=1,
            created_at=datetime.now(timezone.utc),
        )

        self._save_or_reinforce_rule(rule)
        logger.info(
            f" [EDIT-DELTA] Synthesized new BehavioralRule {rule.rule_id} for '{trigger}': {action_directive}"
        )
        return rule

    def _save_or_reinforce_rule(self, rule: BehavioralRule) -> None:
        with sqlite3.connect(self.db_path) as conn:
            # Check if similar trigger exists to reinforce weight
            cursor = conn.cursor()
            cursor.execute(
                "SELECT rule_id, weight, reinforcement_count FROM behavioral_rules WHERE trigger_pattern = ?",
                (rule.trigger_pattern,),
            )
            existing = cursor.fetchone()
            if existing:
                rule_id, current_weight, count = existing
                conn.execute(
                    """
                    UPDATE behavioral_rules
                    SET weight = ?, reinforcement_count = ?, action_directive = ?, inferred_delta_rationale = ?, last_triggered_at = ?
                    WHERE rule_id = ?
                    """,
                    (
                        min(5.0, current_weight + 0.5),
                        count + 1,
                        rule.action_directive,
                        rule.inferred_delta_rationale,
                        datetime.now(timezone.utc).isoformat(),
                        rule_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO behavioral_rules (
                        rule_id, category, trigger_pattern, action_directive,
                        original_draft_sample, user_revised_sample,
                        inferred_delta_rationale, weight, reinforcement_count,
                        created_at, last_triggered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rule.rule_id,
                        rule.category.value,
                        rule.trigger_pattern,
                        rule.action_directive,
                        rule.original_draft_sample,
                        rule.user_revised_sample,
                        rule.inferred_delta_rationale,
                        rule.weight,
                        rule.reinforcement_count,
                        rule.created_at.isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            conn.commit()

    def get_applicable_rules(self, category: Optional[BehavioralCategory] = None) -> List[BehavioralRule]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if category:
                cursor.execute(
                    "SELECT * FROM behavioral_rules WHERE category = ? ORDER BY weight DESC",
                    (category.value,),
                )
            else:
                cursor.execute("SELECT * FROM behavioral_rules ORDER BY weight DESC")
            rows = cursor.fetchall()
            return [
                BehavioralRule(
                    rule_id=r["rule_id"],
                    category=BehavioralCategory(r["category"]),
                    trigger_pattern=r["trigger_pattern"],
                    action_directive=r["action_directive"],
                    original_draft_sample=r["original_draft_sample"],
                    user_revised_sample=r["user_revised_sample"],
                    inferred_delta_rationale=r["inferred_delta_rationale"],
                    weight=r["weight"],
                    reinforcement_count=r["reinforcement_count"],
                    created_at=datetime.fromisoformat(r["created_at"]),
                    last_triggered_at=datetime.fromisoformat(r["last_triggered_at"]) if r["last_triggered_at"] else None,
                )
                for r in rows
            ]
