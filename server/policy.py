#
# SecondLine — agent policy (the thing the self-improvement loop edits).
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""The agent's editable policy.

This is the surface the auto-improvement loop (eval/improve.py) patches. A
failing eval becomes one of four fix types, each of which lands here:

    1. prompt patch      -> system_prompt_extra (extra guidance lines)
    2. memory update      -> handled in backend (preferences)
    3. validation rule    -> boolean flags like enforce_allergens
    4. escalation rule    -> escalation_keywords / low_confidence_escalation

Both the live bot (bot.py) and the eval harness load policy from the same JSON
file, so "before vs after auto-improvement" reflects a real, inspectable diff —
not a story we tell the judges.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

POLICY_PATH = os.environ.get(
    "SECONDLINE_POLICY", str(Path(__file__).parent / "agent_policy.json")
)

# v1 baseline — deliberately missing safety rules so the eval can FIND failures
# and the improvement loop can demonstrably fix them.
DEFAULT_POLICY: dict = {
    "version": 1,
    "enforce_allergens": False,        # validation rule (added by improve loop)
    "confirm_before_order": True,
    "escalation_keywords": ["complaint", "manager", "wrong order", "refund"],
    "low_confidence_escalation": False,  # escalation rule (added by improve loop)
    "system_prompt_extra": "",          # prompt patches accumulate here
    "faq": {
        "hours": "We're open nine to six, Monday through Saturday.",
        "delivery_area": "We deliver anywhere within ten miles of the shop.",
        "payment": "We take all major cards over the phone.",
    },
}


def load_policy() -> dict:
    p = Path(POLICY_PATH)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            # merge over defaults so new keys always exist
            merged = {**DEFAULT_POLICY, **data}
            merged["faq"] = {**DEFAULT_POLICY["faq"], **data.get("faq", {})}
            return merged
        except Exception:
            pass
    return dict(DEFAULT_POLICY)


def save_policy(policy: dict) -> None:
    Path(POLICY_PATH).write_text(json.dumps(policy, indent=2))


def reset_policy() -> dict:
    save_policy(DEFAULT_POLICY)
    return dict(DEFAULT_POLICY)
