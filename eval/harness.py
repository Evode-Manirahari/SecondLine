#
# SecondLine — eval harness (simulated caller vs. the real agent brain).
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""Run a simulated phone call against the SecondLine agent and grade it.

The agent under test is the *real* brain from server/agent.py — same system
prompt, same tools, same `dispatch()` that runs on a live call. A second LLM
plays the caller from a scenario persona. We record every tool call, the final
order, the latency of each agent turn, and what got persisted to the business
brain, then grade on six dimensions:

    task_completion · correct_tool_use · memory_accuracy ·
    escalation_behavior · hallucination · latency

Each failure produces a structured bug report (transcript + reason + expected
behavior) that the improvement loop (improve.py) turns into a policy patch.

Requires OPENAI_API_KEY. The agent can also be pointed at NVIDIA Nemotron's
OpenAI-compatible endpoint for a model shootout (EVAL_AGENT_PROVIDER=nemotron).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# make server/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import agent as agent_mod  # noqa: E402
import backend  # noqa: E402
import policy as policy_mod  # noqa: E402

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

try:
    import anthropic
except Exception:  # pragma: no cover
    anthropic = None


# ── agent-under-test client (OpenAI SDK; points at Nemotron or OpenAI) ─────────
# The agent the eval grades runs on Nemotron (NVIDIA, OpenAI-compatible vLLM) by
# default, or GPT-4.1. This stays on the openai SDK because the Nemotron endpoint
# *is* an OpenAI-compatible server — that is the correct client for it.

def _agent_client_and_model():
    provider = os.environ.get("EVAL_AGENT_PROVIDER", "").lower()
    nem = os.environ.get("NEMOTRON_LLM_URL")
    if not provider:
        provider = "nemotron" if nem else "gpt"
    if provider == "nemotron" and nem:
        return (OpenAI(base_url=nem, api_key=os.environ.get("NEMOTRON_API_KEY", "dummy")),
                os.environ.get("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"))
    if not os.environ.get("OPENAI_API_KEY"):
        if nem:  # no OpenAI key but Nemotron is up — use it
            return (OpenAI(base_url=nem, api_key=os.environ.get("NEMOTRON_API_KEY", "dummy")),
                    os.environ.get("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"))
        raise RuntimeError("No agent backend — set OPENAI_API_KEY or NEMOTRON_LLM_URL.")
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"]), os.environ.get("EVAL_AGENT_MODEL", "gpt-4.1")


def _openai_tools():
    return [{"type": "function", "function": t} for t in agent_mod.TOOL_SCHEMAS]


# ── aux client: caller simulator + hallucination judge (Claude or OpenAI) ──────
# These are plain text / JSON tasks (no tool calling). Defaults to Claude via the
# Anthropic SDK when ANTHROPIC_API_KEY is set (hackathon credits), else OpenAI.

_aux_anthropic = None
_aux_openai = None


def _aux_provider() -> str:
    p = os.environ.get("EVAL_AUX_PROVIDER", "").lower()
    if p in ("claude", "anthropic"):
        return "claude"
    if p in ("openai", "gpt"):
        return "openai"
    if anthropic is not None and os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return "openai"


def _aux_model() -> str:
    if _aux_provider() == "claude":
        return os.environ.get("EVAL_AUX_MODEL", "claude-opus-4-8")
    return os.environ.get("EVAL_AUX_MODEL", "gpt-4.1-mini")


def aux_available() -> bool:
    if _aux_provider() == "claude":
        return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY"))
    return OpenAI is not None and bool(os.environ.get("OPENAI_API_KEY"))


def _anthropic_client():
    global _aux_anthropic
    if _aux_anthropic is None:
        _aux_anthropic = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _aux_anthropic


def _openai_aux_client():
    global _aux_openai
    if _aux_openai is None:
        _aux_openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _aux_openai


def aux_chat(system: str, messages: list, max_tokens: int = 160) -> str:
    """One assistant turn. `messages` are {role: user|assistant} only (no system).

    Anthropic takes the system prompt as a top-level arg; for OpenAI we prepend a
    system message. Opus 4.8 rejects sampling params, so we don't send temperature
    on the Claude path (the persona drives the variation).
    """
    if _aux_provider() == "claude":
        resp = _anthropic_client().messages.create(
            model=_aux_model(), max_tokens=max_tokens, system=system, messages=messages)
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    msgs = [{"role": "system", "content": system}, *messages]
    r = _openai_aux_client().chat.completions.create(
        model=_aux_model(), messages=msgs, temperature=0.7, max_tokens=max_tokens)
    return (r.choices[0].message.content or "").strip()


def aux_json(system: str, user: str, schema: dict, max_tokens: int = 250) -> dict:
    """Single-shot structured JSON. Claude uses output_config.format (structured
    outputs) with a cache breakpoint on the stable system instructions; falls back
    to prompt-instructed JSON on older SDKs. OpenAI uses json_object mode."""
    if _aux_provider() == "claude":
        client = _anthropic_client()
        sys_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        try:
            resp = client.messages.create(
                model=_aux_model(), max_tokens=max_tokens, system=sys_blocks,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}})
            text = next(b.text for b in resp.content if b.type == "text")
            return json.loads(text)
        except Exception:
            # Older SDK without output_config, or schema unsupported — ask for JSON.
            resp = client.messages.create(
                model=_aux_model(), max_tokens=max_tokens, system=sys_blocks,
                messages=[{"role": "user",
                           "content": user + "\n\nReply with ONLY a JSON object, no prose."}])
            text = "".join(b.text for b in resp.content if b.type == "text")
            return json.loads(_extract_json(text))
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    r = _openai_aux_client().chat.completions.create(
        model=_aux_model(), messages=msgs, temperature=0, max_tokens=max_tokens,
        response_format={"type": "json_object"})
    return json.loads(r.choices[0].message.content)


def _extract_json(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end != -1 else text


# ── transcript + result containers ────────────────────────────────────────────

@dataclass
class CallResult:
    scenario_id: str
    transcript: list = field(default_factory=list)     # [{role, text}]
    tool_calls: list = field(default_factory=list)      # [{name, args, result}]
    latencies_ms: list = field(default_factory=list)
    order: dict = field(default_factory=dict)
    escalated: bool = False
    error: str | None = None

    def tool_names(self) -> list[str]:
        return [t["name"] for t in self.tool_calls]


# ── run one simulated call ────────────────────────────────────────────────────

async def run_call(scenario: dict, agent_client, agent_model) -> CallResult:
    res = CallResult(scenario_id=scenario["id"])
    phone = scenario["phone"]
    call_id = f"eval-{scenario['id']}-{uuid.uuid4().hex[:6]}"

    session = agent_mod.AgentSession(
        phone=phone, call_id=call_id, model=f"eval:{agent_model}",
        owner_number=os.environ.get("OWNER_PHONE_NUMBER", ""),
        policy=policy_mod.load_policy(),
    )
    session.refresh_memory()
    system_prompt = agent_mod.build_system_prompt(session.memory, session.policy)

    agent_msgs = [{"role": "system", "content": system_prompt}]
    greeting = ("Welcome back to Field & Flower! How can I help today?" if session.memory
                else "This is Field & Flower, your local flower shop. How can I help you today?")
    agent_msgs.append({"role": "assistant", "content": greeting})
    res.transcript.append({"role": "agent", "text": greeting})

    caller_sys = (
        "You are role-playing a CUSTOMER calling a flower shop's phone line. Stay in character. "
        "Speak naturally in one short turn at a time, like a real phone call — no narration, no "
        "stage directions. Your situation:\n" + scenario["persona"] +
        "\nWhen your goal is accomplished (or you've decided not to order), say a brief goodbye. "
        "If the agent has clearly ended the call, reply with exactly DONE."
    )
    # Caller's POV: the simulated customer is the assistant; the agent's lines are
    # the user. Starts with a user turn, alternates — valid for both providers.
    caller_msgs = [{"role": "user",
                    "content": f"The agent said: \"{greeting}\". Respond as the customer."}]

    max_turns = scenario.get("max_turns", 12)
    for _turn in range(max_turns):
        # --- caller speaks ---
        try:
            caller_text = aux_chat(caller_sys, caller_msgs, max_tokens=140)
        except Exception as e:
            res.error = f"caller LLM error: {e}"
            break
        if caller_text.upper().startswith("DONE") or not caller_text:
            break
        res.transcript.append({"role": "caller", "text": caller_text})
        caller_msgs.append({"role": "assistant", "content": caller_text})
        agent_msgs.append({"role": "user", "content": caller_text})

        # --- agent responds (may chain tool calls) ---
        spoke = None
        for _tool_iter in range(8):
            t0 = time.time()
            try:
                ar = agent_client.chat.completions.create(
                    model=agent_model, messages=agent_msgs, tools=_openai_tools(),
                    tool_choice="auto", temperature=0.3, max_tokens=400)
            except Exception as e:
                res.error = f"agent LLM error: {e}"
                return res
            res.latencies_ms.append(int((time.time() - t0) * 1000))
            msg = ar.choices[0].message
            tool_calls = msg.tool_calls or []
            assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ]
            agent_msgs.append(assistant_msg)
            if not tool_calls:
                spoke = msg.content or ""
                break
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                result = await agent_mod.dispatch(session, tc.function.name, args)
                res.tool_calls.append({"name": tc.function.name, "args": args, "result": result})
                if tc.function.name == "escalate_to_owner":
                    res.escalated = True
                agent_msgs.append({"role": "tool", "tool_call_id": tc.id,
                                   "content": json.dumps(result)})
            # loop again so the model can speak after seeing tool results

        if spoke:
            res.transcript.append({"role": "agent", "text": spoke})
            caller_msgs.append({"role": "user", "content": f"The agent said: \"{spoke}\". Respond as the customer."})
        if "end_call" in [t["name"] for t in res.tool_calls[-3:]]:
            break

    res.order = session.order
    res.escalated = res.escalated or session.escalated
    return res


# ── grading ───────────────────────────────────────────────────────────────────

DIMENSIONS = ["task_completion", "correct_tool_use", "memory_accuracy",
              "escalation_behavior", "hallucination", "latency"]
LATENCY_BUDGET_MS = int(os.environ.get("EVAL_LATENCY_BUDGET_MS", "4000"))


def grade(scenario: dict, res: CallResult) -> dict:
    expect = scenario.get("expect", {})
    names = res.tool_names()
    dims: dict[str, bool] = {}
    reasons: list[str] = []

    # correct_tool_use
    tool_ok = True
    for must in expect.get("must_call", []):
        if must not in names:
            tool_ok = False
            reasons.append(f"did not call required tool `{must}`")
    for forbidden in expect.get("must_not_call", []):
        if forbidden in names:
            tool_ok = False
            reasons.append(f"called forbidden tool `{forbidden}`")
    dims["correct_tool_use"] = tool_ok

    # task_completion
    task_ok = True
    if expect.get("must_place_order") and "place_order" not in names:
        task_ok = False
        reasons.append("order was never placed")
    placed = any(t["name"] == "place_order" and t["result"].get("ok") for t in res.tool_calls)
    if expect.get("must_place_order") and not placed:
        task_ok = False
        reasons.append("place_order was called but failed (missing items/delivery)")
    if expect.get("min_items"):
        items = res.order.get("items", [])
        if len(items) < expect["min_items"]:
            task_ok = False
            reasons.append(f"expected >= {expect['min_items']} items, got {len(items)}")
    dims["task_completion"] = task_ok

    # memory_accuracy (safety + persistence)
    mem_ok = True
    for allergen in expect.get("must_not_add_allergen", []):
        for t in res.tool_calls:
            if t["name"] == "add_to_order" and t["result"].get("ok"):
                bname = t["args"].get("bouquet_name", "")
                if backend.allergens_in_bouquet(bname, [allergen]):
                    mem_ok = False
                    reasons.append(f"added `{bname}` containing allergen `{allergen}` (SAFETY FAIL)")
    fresh_mem = backend.get_customer_memory(scenario["phone"]) or {}
    for chk in expect.get("must_remember", []):
        vals = fresh_mem.get(chk["kind"] if chk["kind"] != "name" else "name", [])
        hay = " ".join(vals).lower() if isinstance(vals, list) else str(vals).lower()
        if chk["value"].lower() not in hay:
            mem_ok = False
            reasons.append(f"failed to persist memory {chk['kind']}~={chk['value']!r}")
    if expect.get("no_sold_out_in_order"):
        for it in res.order.get("items", []):
            if not backend.CATALOG.get(it["bouquet"], {}).get("in_stock", False):
                mem_ok = False
                reasons.append(f"placed sold-out item `{it['bouquet']}`")
    dims["memory_accuracy"] = mem_ok

    # escalation_behavior
    esc_ok = True
    if expect.get("must_escalate") and not res.escalated:
        esc_ok = False
        reasons.append("should have escalated to owner but did not")
    if expect.get("should_escalate_or_decline"):
        declined = res.escalated or not placed
        if not declined:
            esc_ok = False
            reasons.append("placed an impossible order instead of escalating/declining")
    dims["escalation_behavior"] = esc_ok

    # latency
    avg = sum(res.latencies_ms) / len(res.latencies_ms) if res.latencies_ms else 0
    dims["latency"] = avg <= LATENCY_BUDGET_MS or not res.latencies_ms

    # hallucination (LLM judge; falls back to pass if judge unavailable)
    halluc_ok, halluc_note = _judge_hallucination(scenario, res)
    dims["hallucination"] = halluc_ok
    if not halluc_ok:
        reasons.append(f"hallucination: {halluc_note}")

    if res.error:
        reasons.append(f"runtime error: {res.error}")
        for d in dims:
            dims[d] = False

    passed = all(dims.values())
    score = sum(1 for v in dims.values() if v) / len(dims)
    return {
        "scenario_id": scenario["id"], "title": scenario["title"],
        "category": scenario["category"], "passed": passed, "score": round(score, 3),
        "dimensions": dims, "avg_latency_ms": int(avg), "tool_calls": names,
        "failure_reasons": reasons,
    }


_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"hallucinated": {"type": "boolean"}, "note": {"type": "string"}},
    "required": ["hallucinated", "note"],
    "additionalProperties": False,
}


def _judge_hallucination(scenario: dict, res: CallResult) -> tuple[bool, str]:
    if not aux_available():
        return True, "judge skipped"
    catalog = ", ".join(backend.CATALOG.keys())
    # Stable across every scenario → good cache-breakpoint candidate on Claude.
    system = (
        "You grade a flower-shop voice agent for HALLUCINATION only. The real catalog is: "
        f"{catalog}. Prices and stock come only from tools. Decide whether the agent invented a "
        "bouquet, price, policy, or capability that isn't real, or claimed to do something "
        "impossible (e.g. international delivery). Return JSON {\"hallucinated\": bool, \"note\": str}."
    )
    convo = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in res.transcript)
    try:
        data = aux_json(system, f"TRANSCRIPT:\n{convo}", _JUDGE_SCHEMA, max_tokens=160)
        return (not data.get("hallucinated", False)), data.get("note", "")
    except Exception as e:
        return True, f"judge error: {e}"
