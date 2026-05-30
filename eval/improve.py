#
# SecondLine — auto-improvement engine.
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""Turn eval failures into concrete, inspectable fixes.

This is the part of SecondLine that makes the product get *measurably* better
instead of relying on vibes. Each failure report from the harness maps to one of
four fix types, and every fix lands in the agent policy (server/agent_policy.json)
that both the live bot and the eval harness read:

    1. validation rule   — e.g. enable the allergen guard in add_to_order
    2. escalation rule    — e.g. escalate on low confidence / impossible asks
    3. prompt patch       — targeted guidance appended to the system prompt
    4. memory rule        — reinforce update_customer_memory usage

`propose_patches()` is deterministic (auditable for the judges). It returns a
list of patches with a rationale and the source failure, so the demo can show
exactly *why* each change was made.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import policy as policy_mod  # noqa: E402


PROMPT_PATCHES = {
    "reorder": "Returning callers who say 'same as last time' should be handled with reorder_last "
               "before asking for anything else.",
    "memory": "Whenever a caller states an allergy, a flower they dislike, or a standing "
              "preference, immediately call update_customer_memory — do not just acknowledge it "
              "verbally. Allergies are mandatory to record.",
    "text": "When a caller asks you to text or send them anything, call send_customer_text in that "
            "turn rather than promising to do it.",
    "escalate": "If a request is impossible for a local shop (e.g. international or sub-hour "
                "delivery), or the caller is upset, do not improvise — call escalate_to_owner and "
                "tell the caller a human will follow up.",
}


def propose_patches(report: dict) -> list[dict]:
    """Inspect a full eval report and propose policy patches for the failures."""
    patches: list[dict] = []
    seen: set[str] = set()

    def add(key: str, ptype: str, change: dict, rationale: str, src: str):
        if key in seen:
            return
        seen.add(key)
        patches.append({"type": ptype, "change": change, "rationale": rationale,
                        "source_failure": src})

    for r in report.get("results", []):
        if r["passed"]:
            continue
        for reason in r["failure_reasons"]:
            low = reason.lower()
            if "allergen" in low or "safety fail" in low:
                add("enforce_allergens", "validation_rule",
                    {"enforce_allergens": True},
                    "Allergen safety failure detected — enable the add_to_order allergen guard so "
                    "the agent can never add a bouquet containing a known allergen.",
                    f"{r['scenario_id']}: {reason}")
            elif "persist memory" in low:
                add("prompt_memory", "prompt_patch",
                    {"system_prompt_extra_append": PROMPT_PATCHES["memory"]},
                    "Agent acknowledged a constraint but didn't persist it — reinforce "
                    "update_customer_memory in the prompt.",
                    f"{r['scenario_id']}: {reason}")
            elif "escalat" in low or "impossible" in low:
                add("low_confidence_escalation", "escalation_rule",
                    {"low_confidence_escalation": True,
                     "system_prompt_extra_append": PROMPT_PATCHES["escalate"]},
                    "Agent tried to handle something it shouldn't — turn on low-confidence "
                    "escalation and add an escalation guidance line.",
                    f"{r['scenario_id']}: {reason}")
            elif "reorder_last" in low:
                add("prompt_reorder", "prompt_patch",
                    {"system_prompt_extra_append": PROMPT_PATCHES["reorder"]},
                    "Repeat caller flow missed reorder_last — add explicit guidance.",
                    f"{r['scenario_id']}: {reason}")
            elif "send_customer_text" in low:
                add("prompt_text", "prompt_patch",
                    {"system_prompt_extra_append": PROMPT_PATCHES["text"]},
                    "Agent promised a text but didn't call send_customer_text — add guidance.",
                    f"{r['scenario_id']}: {reason}")
            elif "sold-out" in low or "sold out" in low:
                add("prompt_soldout", "prompt_patch",
                    {"system_prompt_extra_append": "Always check_availability before adding an item "
                     "the caller named; never add a sold-out bouquet."},
                    "Sold-out item slipped into an order — require an availability check.",
                    f"{r['scenario_id']}: {reason}")
    return patches


def apply_patches(patches: list[dict]) -> dict:
    """Apply patches to the live policy file and return the new policy."""
    pol = policy_mod.load_policy()
    for p in patches:
        change = p["change"]
        for k, v in change.items():
            if k == "system_prompt_extra_append":
                existing = pol.get("system_prompt_extra", "")
                if v not in existing:
                    pol["system_prompt_extra"] = (existing + " " + v).strip()
            else:
                pol[k] = v
    pol["version"] = pol.get("version", 1) + 1
    policy_mod.save_policy(pol)
    return pol


def summarize(patches: list[dict]) -> str:
    if not patches:
        return "No patches proposed — nothing to improve."
    lines = [f"Proposed {len(patches)} fix(es):"]
    for p in patches:
        lines.append(f"  • [{p['type']}] {p['rationale']}")
        lines.append(f"      from: {p['source_failure']}")
    return "\n".join(lines)
