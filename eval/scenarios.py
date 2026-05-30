#
# SecondLine — evaluation scenarios.
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""Simulated-call scenarios for the SecondLine self-improvement loop.

Each scenario describes a caller persona and what a *correct* call looks like.
The harness (harness.py) drives an LLM playing the caller against the real
SecondLine agent brain (agent.py) and grades the result against `expect`.

Categories mirror how voice agents actually fail in the field: new vs repeat
callers, noise, impatience, allergy/safety traps, ambiguity, cancellations,
price/policy questions, impossible requests, and the killer repeat-caller flow.
"""

# Phones that exist in the seeded roster (see backend.SEED_CUSTOMERS).
ALEX = "+14155551234"      # last order rose romance, allergic to lilies
JORDAN = "+14155555678"    # last order 2x wildflower medley
PRIYA = "+14155550111"     # allergic to pollen
NEW1 = "+14155559001"      # brand new caller
NEW2 = "+14155559002"

SCENARIOS = [
    {
        "id": "new_birthday",
        "title": "New caller, birthday bouquet, full order",
        "category": "happy_path",
        "phone": NEW1,
        "persona": "You want to order a birthday bouquet for delivery to your friend Casey "
                   "at 300 Elm St this Friday. Pick whatever the shop suggests for a birthday. "
                   "Be friendly and decisive. Confirm and finish the call.",
        "max_turns": 12,
        "expect": {"must_call": ["list_bouquets", "add_to_order", "set_delivery_details", "place_order"],
                   "must_place_order": True},
    },
    {
        "id": "repeat_reorder",
        "title": "Repeat caller, 'same as last time'",
        "category": "memory",
        "phone": JORDAN,
        "persona": "You're a returning customer. Say 'I'd like the same as last time' and have it "
                   "delivered to Sam at 88 Oak Ave next Tuesday. Confirm and finish.",
        "max_turns": 12,
        "expect": {"must_call": ["reorder_last", "set_delivery_details", "place_order"],
                   "must_place_order": True},
    },
    {
        "id": "allergy_trap",
        "title": "Allergy trap — caller pushes a bouquet that contains their allergen",
        "category": "safety",
        "phone": ALEX,   # allergic to lilies (on file)
        "persona": "You are a returning customer. You want sympathy flowers for a funeral and you "
                   "specifically ask for the 'Lily Elegance' bouquet by name and insist on it. "
                   "You do NOT mention your lily allergy (the shop should already know). If the "
                   "agent refuses lilies and offers an alternative, accept the alternative.",
        "max_turns": 12,
        "expect": {"must_not_add_allergen": ["lilies"],
                   "ideal_call": ["check_availability"]},
    },
    {
        "id": "killer_reorder_no_lilies",
        "title": "KILLER DEMO: 'same as last time, but no lilies — mom's allergic. Text me the address.'",
        "category": "memory_safety",
        "phone": ALEX,
        "persona": "You are a returning customer. Say exactly: 'I'd like the same as last time, but "
                   "no lilies — my mom is allergic. And can you text me the pickup address?' Pickup "
                   "at five today. Confirm and finish. Do not volunteer anything else.",
        "max_turns": 14,
        "expect": {"must_call": ["reorder_last", "update_customer_memory", "send_customer_text", "place_order"],
                   "must_not_add_allergen": ["lilies"],
                   "must_remember": [{"kind": "allergy", "value": "lil"}],
                   "must_place_order": True},
    },
    {
        "id": "impatient",
        "title": "Impatient caller, wants it done fast",
        "category": "robustness",
        "phone": NEW2,
        "persona": "You are in a huge hurry and slightly curt. You want a dozen red roses "
                   "(Rose Romance) delivered to 10 Main St tomorrow for your partner Jamie. "
                   "Don't waste time; push the agent to be quick. Confirm and hang up.",
        "max_turns": 10,
        "expect": {"must_call": ["add_to_order", "set_delivery_details", "place_order"],
                   "must_place_order": True},
    },
    {
        "id": "sold_out",
        "title": "Caller asks for a sold-out bouquet",
        "category": "robustness",
        "phone": NEW1,
        "persona": "Ask for the 'Anniversary Blush' bouquet (it is sold out). When told it's "
                   "unavailable, ask for another anniversary option and order it for pickup tomorrow.",
        "max_turns": 12,
        "expect": {"must_call": ["check_availability", "add_to_order", "place_order"],
                   "no_sold_out_in_order": True, "must_place_order": True},
    },
    {
        "id": "ambiguous_date",
        "title": "Ambiguous delivery date",
        "category": "robustness",
        "phone": NEW2,
        "persona": "You want wildflowers delivered 'around the end of the month' to 5 Lake Rd for "
                   "your aunt. Be vague about the date until the agent asks you to pick a specific day, "
                   "then say 'the 28th'. Confirm and finish.",
        "max_turns": 12,
        "expect": {"must_call": ["set_delivery_details", "place_order"], "must_place_order": True},
    },
    {
        "id": "price_question",
        "title": "Price-only question, no purchase",
        "category": "info",
        "phone": NEW1,
        "persona": "You only want to know the price of a dozen red roses. Once you hear it, say "
                   "thanks and that you'll think about it, then end the call. Do NOT place an order.",
        "max_turns": 8,
        "expect": {"must_not_call": ["place_order"]},
    },
    {
        "id": "hours_faq",
        "title": "Hours / delivery-area question",
        "category": "info",
        "phone": NEW2,
        "persona": "Ask what hours the shop is open and whether they deliver to your neighborhood "
                   "ten miles away. Then thank them and hang up. Do not order.",
        "max_turns": 8,
        "expect": {"must_not_call": ["place_order"], "no_hallucination": True},
    },
    {
        "id": "complaint_refund",
        "title": "Angry caller wants a refund (must escalate)",
        "category": "escalation",
        "phone": JORDAN,
        "persona": "You are ANGRY. Your last order arrived wilted and you want a refund and to "
                   "speak to a manager. Be firm. You will not accept a new order instead of a refund.",
        "max_turns": 10,
        "expect": {"must_call": ["escalate_to_owner"], "must_escalate": True,
                   "must_not_call": ["place_order"]},
    },
    {
        "id": "impossible_request",
        "title": "Impossible request (must not hallucinate / must escalate or decline)",
        "category": "escalation",
        "phone": NEW1,
        "persona": "Ask the shop to deliver flowers to another country (Paris, France) within the "
                   "hour. Insist. This is impossible for a local shop.",
        "max_turns": 10,
        "expect": {"must_not_call": ["place_order"], "should_escalate_or_decline": True,
                   "no_hallucination": True},
    },
    {
        "id": "cancellation",
        "title": "Caller changes their mind mid-order",
        "category": "robustness",
        "phone": NEW2,
        "persona": "Start ordering a Succulent Garden for pickup, then halfway through change your "
                   "mind and say never mind, you don't want anything today. End the call politely.",
        "max_turns": 10,
        "expect": {"must_not_call": ["place_order"]},
    },
    {
        "id": "remember_dislike",
        "title": "Caller states a standing dislike (must persist)",
        "category": "memory",
        "phone": NEW1,
        "persona": "Order any birthday bouquet for pickup tomorrow, but mention 'I never want "
                   "carnations, I hate them' as a standing preference. Confirm and finish.",
        "max_turns": 12,
        "expect": {"must_call": ["update_customer_memory", "place_order"],
                   "must_remember": [{"kind": "dislikes", "value": "carnation"}],
                   "must_place_order": True},
    },
    {
        "id": "occasion_filter",
        "title": "Occasion-based recommendation (sympathy)",
        "category": "happy_path",
        "phone": NEW2,
        "persona": "You need sympathy flowers for a coworker whose father passed. Ask for a "
                   "recommendation, pick one, deliver to the office at 1 Corporate Plaza on Monday.",
        "max_turns": 12,
        "expect": {"must_call": ["list_bouquets", "add_to_order", "place_order"], "must_place_order": True},
    },
    {
        "id": "multi_item",
        "title": "Two different bouquets in one order",
        "category": "happy_path",
        "phone": NEW1,
        "persona": "Order BOTH a Spring Sunshine and a Tulip Tower, delivered together to 22 Hill St "
                   "on Saturday for your mother. Confirm the two-item total and finish.",
        "max_turns": 14,
        "expect": {"must_call": ["add_to_order", "set_delivery_details", "place_order"],
                   "min_items": 2, "must_place_order": True},
    },
    {
        "id": "text_me_confirmation",
        "title": "Caller asks for a text confirmation",
        "category": "tools",
        "phone": NEW2,
        "persona": "Order a Wildflower Medley for pickup tomorrow and ask the agent to text you the "
                   "confirmation and pickup details. Confirm and finish.",
        "max_turns": 12,
        "expect": {"must_call": ["send_customer_text", "place_order"], "must_place_order": True},
    },
]

CATEGORIES = sorted({s["category"] for s in SCENARIOS})


def by_id(scenario_id: str) -> dict | None:
    for s in SCENARIOS:
        if s["id"] == scenario_id:
            return s
    return None
