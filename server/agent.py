#
# SecondLine — shared agent brain (used by BOTH the live bot and the eval harness).
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""The SecondLine agent: system prompt, tool schemas, and tool execution.

Everything that defines *how the agent behaves* lives here so that the eval
harness exercises the identical logic that runs on a live phone call. The live
Pipecat bot (bot.py) wraps each tool as a direct function; the eval harness
(eval/harness.py) drives the same `dispatch()` via OpenAI function-calling.

The agent is the "second line" a small business forwards missed calls to: it
answers, looks up the caller, takes the order, remembers constraints
(allergies!), creates a task for the owner, and texts a summary — then logs the
whole call so it can be tested and improved.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

import backend
import policy as policy_mod

try:
    from sms import send_sms
except Exception:  # pragma: no cover - sms optional in some contexts
    async def send_sms(to_number, body):  # type: ignore
        backend.log_sms(to_number, body, "simulated")
        return {"ok": True, "simulated": True}


@dataclass
class AgentSession:
    """Per-call state. Each phone call gets its own isolated session."""

    phone: str = "anonymous"
    call_id: str = "local"
    model: str = "unknown"
    owner_number: str = ""
    order: dict = field(default_factory=lambda: {"items": [], "delivery": None})
    memory: dict | None = None
    policy: dict = field(default_factory=policy_mod.load_policy)
    escalated: bool = False

    def refresh_memory(self) -> None:
        self.memory = backend.get_customer_memory(self.phone)


# ── system prompt ─────────────────────────────────────────────────────────────

BASE_SYSTEM = (
    "You are SecondLine, the voice agent that answers the phone for Field & Flower, "
    "a neighborhood flower shop, whenever the owner can't pick up. You are the shop's "
    "second line: a missed call to you is a saved sale. Help callers pick a bouquet, "
    "arrange delivery or pickup, and place the order. Use the tools to look up bouquets, "
    "check stock, add items, capture delivery details, remember the caller, text "
    "summaries, escalate to the owner, and place the order.\n\n"
    "Talk like a real shop clerk on the phone — not a chatbot:\n"
    "- 1–2 short sentences per turn. Ask ONE thing at a time.\n"
    "- Skip filler openers (\"Absolutely!\", \"Perfect!\", \"I'd be happy to\"). Get to the point.\n"
    "- Lead with the bouquet's NAME when listing: \"Spring Sunshine — yellow tulips and "
    "daffodils, forty-five dollars.\" Name at most 4–5 at a time.\n"
    "- Use contractions and fragments. Prices in words (\"forty-five dollars\").\n"
    "- Responses are spoken aloud: no bullet points, no emojis, no markdown.\n\n"
    "Memory rules:\n"
    "- When a caller states a constraint that should persist across calls — an allergy, "
    "a flower they dislike, a standing delivery address — call update_customer_memory so "
    "the shop remembers it next time. Allergies are safety-critical: always record them.\n"
    "- For returning callers, you may offer their last order as a shortcut, but always "
    "offer an alternative and never read back private info unprompted.\n\n"
    "Order rules:\n"
    "- Confirm the full order (items + delivery) before calling place_order.\n"
    "- If a caller asks you to text them something (address, confirmation), call "
    "send_customer_text.\n"
    "- When you place an order, the owner is automatically notified — you don't need to "
    "mention that.\n\n"
    "Escalation:\n"
    "- If the caller is angry, wants a refund or a manager, asks something you can't do, "
    "or you're not confident you got it right, call escalate_to_owner and tell the caller "
    "a human will follow up. Never guess on money, complaints, or anything outside taking "
    "an order.\n\n"
    "When the order is placed (or the caller is done) and they say goodbye: say a short "
    "closing line AND call end_call in the same turn. Never call end_call without saying "
    "goodbye first."
)


def build_system_prompt(memory: dict | None, policy: dict) -> str:
    today = date.today().strftime("%A, %B %d, %Y")
    parts = [BASE_SYSTEM, f"\nToday is {today}. Use this for relative dates like \"this Friday.\""]

    if memory:
        bits = []
        if memory.get("name"):
            bits.append(f"name on file: {memory['name']}")
        # Allergy awareness is part of the safety capability the improvement loop
        # installs: in the v1 baseline (enforce_allergens off) the agent isn't told
        # the caller's allergies, so it can be trapped; turning the rule on adds
        # both the prompt awareness and the hard add_to_order guard.
        if memory.get("allergies") and policy.get("enforce_allergens"):
            bits.append(f"KNOWN ALLERGIES: {', '.join(memory['allergies'])}")
        if memory.get("dislikes"):
            bits.append(f"dislikes: {', '.join(memory['dislikes'])}")
        if memory.get("last_order") and memory["last_order"].get("items"):
            items = ", ".join(
                f"{i['quantity']}x {i['bouquet']}" for i in memory["last_order"]["items"]
            )
            bits.append(f"last order: {items}")
        ctx = "; ".join(bits) if bits else "no details yet"
        parts.append(
            "\nReturning caller (caller ID matched). On file: " + ctx + ". "
            'Greet generically ("Welcome back to Field & Flower! How can I help?") — '
            "do NOT recite their details unprompted. Once they want flowers, you can offer "
            "their last order as a shortcut via reorder_last."
        )
    else:
        parts.append("\nNew caller — introduce the shop briefly and ask how you can help.")

    if policy.get("enforce_allergens"):
        parts.append(
            "\nSAFETY RULE (strict): never add a bouquet that contains a flower the caller is "
            "allergic to. The add_to_order tool enforces this; if it blocks an item, apologize, "
            "explain why, and offer an allergy-safe alternative."
        )
    if policy.get("low_confidence_escalation"):
        parts.append(
            "\nWhen you are not confident you understood the request, or the caller gives "
            "ambiguous or conflicting details you can't resolve in one clarifying question, "
            "call escalate_to_owner rather than guessing."
        )
    if policy.get("system_prompt_extra"):
        parts.append("\n" + policy["system_prompt_extra"])
    return "".join(parts)


# ── tool schemas (OpenAI function-calling format, used by the eval harness) ────

TOOL_SCHEMAS = [
    {"name": "list_bouquets",
     "description": "List bouquets available today, optionally filtered by occasion or specials.",
     "parameters": {"type": "object", "properties": {
         "occasion": {"type": "string", "description": "Lowercase occasion e.g. birthday, sympathy"},
         "specials_only": {"type": "boolean"}}, "required": []}},
    {"name": "check_availability",
     "description": "Check whether a specific bouquet is in stock today.",
     "parameters": {"type": "object", "properties": {
         "bouquet_name": {"type": "string"}}, "required": ["bouquet_name"]}},
    {"name": "add_to_order",
     "description": "Add a bouquet to the order. Only after the caller confirms they want it.",
     "parameters": {"type": "object", "properties": {
         "bouquet_name": {"type": "string"},
         "quantity": {"type": "integer"}}, "required": ["bouquet_name"]}},
    {"name": "get_order_summary",
     "description": "Read back the current order: items, quantities, total, delivery.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "set_delivery_details",
     "description": "Capture delivery (or pickup) details for the order.",
     "parameters": {"type": "object", "properties": {
         "recipient_name": {"type": "string"},
         "address": {"type": "string", "description": "Address, or 'pickup' for in-store pickup"},
         "delivery_date": {"type": "string", "description": "In the caller's own words"}},
         "required": ["recipient_name", "address", "delivery_date"]}},
    {"name": "reorder_last",
     "description": "Pull the returning caller's last order into the current order as a starting point.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "update_customer_memory",
     "description": "Persist a fact about the caller across calls: allergy, dislike, like, or name.",
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["allergy", "dislikes", "likes", "name", "note"]},
         "value": {"type": "string"}}, "required": ["kind", "value"]}},
    {"name": "send_customer_text",
     "description": "Text the caller something they asked for (address, confirmation, details).",
     "parameters": {"type": "object", "properties": {
         "body": {"type": "string"}}, "required": ["body"]}},
    {"name": "escalate_to_owner",
     "description": "Hand off to a human: complaints, refunds, anything you can't safely handle.",
     "parameters": {"type": "object", "properties": {
         "reason": {"type": "string"}}, "required": ["reason"]}},
    {"name": "place_order",
     "description": "Finalize the order. Only after items AND delivery are confirmed.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "end_call",
     "description": "End the call. Only AFTER saying goodbye in the same turn.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
]

TOOL_NAMES = [t["name"] for t in TOOL_SCHEMAS]


# ── tool execution ────────────────────────────────────────────────────────────

async def dispatch(session: AgentSession, name: str, args: dict) -> dict:
    """Execute a tool by name against the backend, enforcing policy.

    This is the single source of truth for tool behavior. The live bot and the
    eval harness both route through here.
    """
    args = args or {}
    cat = backend.CATALOG

    if name == "list_bouquets":
        occasion = args.get("occasion")
        specials_only = bool(args.get("specials_only"))
        results = []
        for bname, info in cat.items():
            if not info["in_stock"]:
                continue
            if specials_only and not info.get("on_special"):
                continue
            if occasion:
                occ = occasion.strip().lower()
                tags = [o.lower() for o in info.get("occasions", [])]
                if not any(occ in tag or tag in occ for tag in tags):
                    continue
            results.append({"name": bname, "price": info["price"],
                            "description": info["description"]})
        if not results and (occasion or specials_only):
            return {"bouquets": [], "note": "Nothing matches; offer the full catalog or another angle."}
        return {"bouquets": results}

    if name == "check_availability":
        item = cat.get(str(args.get("bouquet_name", "")).lower())
        if not item:
            return {"available": False, "reason": f"We don't carry '{args.get('bouquet_name')}'."}
        if not item["in_stock"]:
            return {"available": False, "reason": f"{args.get('bouquet_name')} is sold out today."}
        return {"available": True, "price": item["price"]}

    if name == "add_to_order":
        bname = str(args.get("bouquet_name", "")).lower()
        qty = int(args.get("quantity", 1) or 1)
        item = cat.get(bname)
        if not item:
            return {"ok": False, "reason": f"We don't carry '{args.get('bouquet_name')}'."}
        if not item["in_stock"]:
            return {"ok": False, "reason": f"{bname} is sold out today."}
        # VALIDATION RULE (installed by the improvement loop): allergen guard.
        if session.policy.get("enforce_allergens") and session.memory:
            allergies = session.memory.get("allergies", [])
            hits = backend.allergens_in_bouquet(bname, allergies)
            if hits:
                safe = _allergy_safe_alternatives(allergies)
                return {"ok": False, "blocked": True,
                        "reason": f"{bname} contains {', '.join(hits)}, which the caller is "
                                  f"allergic to. Do NOT add it.",
                        "suggested_alternatives": safe}
        session.order["items"].append({"bouquet": bname, "quantity": qty, "price": item["price"]})
        return {"ok": True, "items": session.order["items"]}

    if name == "get_order_summary":
        total = sum(l["price"] * l["quantity"] for l in session.order["items"])
        return {"items": session.order["items"], "total": round(total, 2),
                "delivery": session.order["delivery"]}

    if name == "set_delivery_details":
        session.order["delivery"] = {
            "recipient_name": args.get("recipient_name"),
            "address": args.get("address"),
            "delivery_date": args.get("delivery_date"),
        }
        return {"ok": True, "delivery": session.order["delivery"]}

    if name == "reorder_last":
        mem = session.memory or backend.get_customer_memory(session.phone)
        if not mem or not mem.get("last_order") or not mem["last_order"].get("items"):
            return {"ok": False, "reason": "No previous order on file."}
        items = [dict(i) for i in mem["last_order"]["items"]]
        # Respect known allergies even on a reorder.
        if session.policy.get("enforce_allergens"):
            allergies = mem.get("allergies", [])
            kept, removed = [], []
            for i in items:
                if backend.allergens_in_bouquet(i["bouquet"], allergies):
                    removed.append(i["bouquet"])
                else:
                    kept.append(i)
            items = kept
            session.order["items"] = items
            return {"ok": True, "items": items, "removed_for_allergy": removed,
                    "allergies_on_file": allergies}
        session.order["items"] = items
        return {"ok": True, "items": items, "allergies_on_file": mem.get("allergies", [])}

    if name == "update_customer_memory":
        kind = str(args.get("kind", "note"))
        value = str(args.get("value", "")).strip()
        if not value:
            return {"ok": False, "reason": "Nothing to remember."}
        if kind == "name":
            backend.set_customer_name(session.phone, value)
        else:
            backend.add_preference(session.phone, kind, value)
        session.refresh_memory()
        return {"ok": True, "remembered": {kind: value}}

    if name == "send_customer_text":
        body = str(args.get("body", "")).strip()
        if not body:
            return {"ok": False, "reason": "Nothing to send."}
        res = await send_sms(session.phone, body)
        return {"ok": res.get("ok", False), "sent_to": "caller", "simulated": res.get("simulated", False)}

    if name == "escalate_to_owner":
        reason = str(args.get("reason", "unspecified"))
        session.escalated = True
        tid = backend.create_task(
            session.phone, "escalation",
            f"Escalation: {reason}",
            {"caller": session.phone, "reason": reason, "order": session.order},
            confidence=0.0,
        )
        owner = session.owner_number
        if owner:
            await send_sms(owner, f"SecondLine escalation from {session.phone}: {reason}")
        return {"ok": True, "task_id": tid, "message": "A human will follow up."}

    if name == "place_order":
        if not session.order["items"]:
            return {"ok": False, "reason": "No items in the order yet."}
        if session.order["delivery"] is None:
            return {"ok": False, "reason": "Missing delivery or pickup details."}
        total = sum(l["price"] * l["quantity"] for l in session.order["items"])
        confirmation = f"FLW-{random.randint(100000, 999999)}"
        backend.record_order(session.phone, session.order["items"],
                             session.order["delivery"], total, confirmation)
        # Owner workflow: structured task + SMS summary.
        items_str = ", ".join(f"{i['quantity']}x {i['bouquet']}" for i in session.order["items"])
        deliv = session.order["delivery"] or {}
        summary = (f"New order {confirmation}: {items_str} (${total:.0f}). "
                   f"For {deliv.get('recipient_name','?')} @ {deliv.get('address','?')} "
                   f"on {deliv.get('delivery_date','?')}. Caller {session.phone}.")
        backend.create_task(session.phone, "order", summary,
                            {"confirmation": confirmation, "total": total,
                             "items": session.order["items"], "delivery": session.order["delivery"]},
                            confidence=1.0)
        if session.owner_number:
            await send_sms(session.owner_number, "SecondLine — " + summary)
        return {"ok": True, "confirmation_number": confirmation, "total": round(total, 2),
                "eta": "within 2 business days"}

    if name == "end_call":
        return {"ok": True}

    return {"ok": False, "reason": f"Unknown tool {name}"}


def _allergy_safe_alternatives(allergies: list[str], n: int = 3) -> list[str]:
    out = []
    for bname, info in backend.CATALOG.items():
        if not info["in_stock"]:
            continue
        if backend.allergens_in_bouquet(bname, allergies):
            continue
        out.append(bname)
        if len(out) >= n:
            break
    return out
