# 🌷 SecondLine

**A self-improving voice agent for the calls small local businesses can't pick up.**

Built at the YC Voice Agents Hackathon (Pipecat · Cekura · NVIDIA · AWS · Twilio).

📞 **Call it live:** **+1 888 778 8643**   ·   🌐 **Site:** https://evode-manirahari.github.io/SecondLine/

---

## 1. What is this?

Small businesses miss ~**62%** of their calls, and ~**85%** of those callers never call back — they just call the next shop. SecondLine is the number a restaurant, florist, or clinic forwards its missed calls to. It:

- **answers naturally** over a real phone line,
- **remembers the caller** (past orders, allergies) by caller ID,
- takes the order through **typed tools** so the model can never invent business state,
- **files a structured task for the owner** (with an SMS summary in production),
- and — the part we care about — **tests, scores, and improves itself** on every call.

It's not a chatbot demo. It's a production loop: real telephony, persistent memory, tool safety, automated test calls, failure analysis, and **measurable** improvement.

---

## 2. Demo video (< 60 seconds)

[![SecondLine — 60-second demo](https://img.youtube.com/vi/NJyHFlViMsk/maxresdefault.jpg)](https://youtu.be/NJyHFlViMsk)

▶️ **https://youtu.be/NJyHFlViMsk**

---

## 3. How we used Cekura, Nemotron, and Pipecat

### Pipecat
Pipecat is the spine. The agent is a Pipecat pipeline — `transport.input → STT → LLM → TTS → transport.output` — with Silero VAD + turn detection, interruptions, and **direct-function tool calls**. We support three transports (Twilio websocket for phone, Daily/WebRTC for browser + automated tests, SmallWebRTC for local dev) and deploy to **Pipecat Cloud**. We started from the *Field & Flower* starter and built the business logic, memory, tools, eval loop, and dashboard on top.

### NVIDIA Nemotron (open weights)
The agent's default brain is **Nemotron-3-Super-120B** (NVIDIA open-weight) served over vLLM on AWS, and we wired up **NVIDIA Nemotron Speech-Streaming ASR**. Nemotron is the agent-under-test in our evaluation harness — the open-weight model our self-improvement loop measured and improved (88% → 94%, unsafe 1 → 0). The architecture is model-agnostic (one env var swaps Nemotron / Claude / GPT-4.1), so we demo on whichever gives the cleanest take, but Nemotron is the default provider and the model we evaluated against.

### Cekura — what we tested and how much we improved
**Goal:** test the agent across realistic missed-call scenarios (new orders, repeat callers, the *allergy-safety* trap, refunds/escalation, manager requests) and turn failures into fixes.

We drove Cekura's API directly: created a Pipecat agent, **auto-generated scenarios**, ran **`run_scenarios_pipecat_v2`** (real WebRTC voice calls against the live agent), and pulled the scored transcripts.

**What Cekura caught (that our local tests couldn't):** the very first Cekura run scored **0%** — every call's transcript read *"the main agent did not speak."* That pinpointed a **production bug**: our deployed bot handled Twilio + local WebRTC but **not** the Daily transport Pipecat Cloud uses for WebRTC sessions, so the agent silently built no pipeline. We added the Daily case, redeployed, and re-ran — the agent went from **silent → fully conversational** (Cekura's transcripts now verify it asking for preferences, acknowledging the recipient's allergen, and stating prices). A real find-fix-verify loop on real automated voice calls.

**Alongside Cekura**, we built a local Cekura-style harness (16 scenarios graded on 6 dimensions: task completion, tool use, memory accuracy, escalation, latency, hallucination). The improvement script turns each failure into one of four fixes — validation rule, escalation rule, prompt patch, or memory update — and re-runs:

| Metric (local harness, Nemotron) | Before | After |
|---|---|---|
| Pass rate | **88%** | **94%** |
| **Unsafe actions** (added an allergen the caller is allergic to) | **1** | **0** |

The headline: the loop caught the agent about to add a **lily** bouquet to a caller with a **lily allergy**, auto-wrote a validation rule that blocks it at the tool layer, and proved it gone.

---

## 4. What we built *new* during the hackathon

Everything in this repo was built **during the hackathon**, on top of the Pipecat *Field & Flower* starter.

**New (built today):**
- SQLite **business brain** + persistent caller-ID memory (customers, allergies, past orders, transcripts, owner tasks)
- **11 typed tools** + a shared `dispatch()` used by *both* the live bot and the eval harness
- **Allergen safety guard** enforced at the tool layer
- The **self-improvement loop** — scenarios, a simulated-caller grader (6 dimensions), and the failure→4-fix-types→re-run engine
- **Real Cekura integration** driven via its REST API (agent + autogen scenarios + run + results)
- **Daily transport** support (the fix Cekura's testing surfaced)
- Provider switching across **Nemotron / Claude / GPT-4.1**
- Owner **dashboard** (FastAPI) — task queue, transcripts, before/after pass rate
- Deploy to **Pipecat Cloud** + a **Twilio** phone number, and this **landing site**

**Borrowed:** the Field & Flower starter (pipeline skeleton, Gradium STT/TTS + Twilio serializer wiring), Cekura's skills/MCP + platform, NVIDIA's hosted Nemotron LLM + ASR endpoints.

---

## 5. Feedback on the tools

### NVIDIA Nemotron
- **Did well:** genuinely fast (TTFB ~0.18s to first token over vLLM), solid reasoning, and it *does* do OpenAI-style tool calling out of the box — impressive for an open-weight model in a real-time voice loop.
- **Could be better:** (1) It tends to **narrate its tool use** ("let me check availability and then add it") instead of acting silently — needs firm prompting to stay concise for voice. (2) It will **loop on tool calls** (re-checking stock) without a hard "check once" rule. (3) Tool-argument formatting was sensitive: with a generic/variadic function signature it emitted nested `{"kwargs": "{...}"}` arguments — explicit typed parameters fixed it, but it was less forgiving than GPT/Claude about loose schemas.

### Cekura
- **The platform earned its keep:** it found a production bug (silent-on-Daily) that our offline tests *could not* — automated voice calls against the real deployment are the real thing. The find→fix→re-run loop is exactly the right shape.
- **Bugs / friction we hit:**
  - The MCP server connected fine (`claude mcp list` ✓) but its tools didn't load into our (orchestrated) Claude Code session, so we **drove the REST API directly** — which worked great once we found `/api/schema/` (the OpenAPI spec is excellent and made integration possible).
  - `POST /test_framework/generate_scenarios/` returns **"obsolete, use generate-bg"** — the docs/MCP still reference the old path.
  - Result payloads from `results-external/{id}` contain **unescaped control characters** in transcript strings, which break strict JSON parsers (had to parse with `strict=False`).
  - Auto-generated scenarios sometimes test **out-of-scope** behaviors (a $30k corporate order, processing refunds) the agent intentionally escalates — so a 0% score can mean "scenario exceeded the agent's design," not "agent broken." A way to scope autogen to the agent's actual capabilities/catalog would help.

### Pipecat / Twilio / Gradium
- **Pipecat:** clean abstractions; the universal `LLMContext` + direct-function tools made multi-provider swaps trivial. **Gap:** the starter handles Twilio + SmallWebRTC but **not the Daily transport** — which is what Pipecat Cloud (and Cekura) use for WebRTC sessions. Including a `DailyRunnerArguments` case in the starter would have saved us the silent-agent bug.
- **Local setup pain (Intel Mac):** `pipecat-ai[silero]` pulls `onnxruntime`/`numba`/`llvmlite` versions with **no x86_64-macOS wheels**, and `daily-python` has none either — local dev on an Intel Mac needed platform-gated dependency overrides. Apple-Silicon/Linux are fine.
- **Twilio:** trial accounts allow **only one number** and that toll-free number can't send SMS, so our owner-SMS is built but shows as a dashboard task until upgrade — worth flagging to hackathon teams up front.

---

## 6. Live links

- 📞 **Phone:** call **+1 888 778 8643** (brief Twilio trial preamble, then the agent)
- 🌐 **Website:** https://evode-manirahari.github.io/SecondLine/
- 🛠️ **Run it yourself:** see [`RUNBOOK.md`](RUNBOOK.md) (local WebRTC, the eval loop, dashboard, and deploy)

---

*Architecture, the eval harness, and the demo script live in [`RUNBOOK.md`](RUNBOOK.md) and [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md). The agent's editable policy (what the improvement loop patches) is [`server/agent_policy.json`](server/agent_policy.json).*
