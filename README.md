# 🌷 SecondLine

**A self-improving voice agent for the calls local businesses are too busy to answer.**

Built for the **YC Voice Agents Hackathon** (Pipecat · Cekura · NVIDIA · AWS · Twilio) on top of the *Field & Flower* starter.

> SecondLine is the line a shop forwards its missed calls to. It answers naturally, **remembers the caller** (including allergies), takes the order, **creates a task for the owner and texts them a summary**, and escalates anything risky to a human. The part that wins: **every call is logged, tested, scored, and used to make the next version measurably better** — not "we improved the prompt and it feels better," but a before/after pass-rate you can read off a dashboard.

---

## The problem is real, and it's expensive

Small local businesses lose money every time the phone rings and no one picks up — and it rings a *lot* more than they answer.

- **62% of calls to small businesses go unanswered**, and the average SMB loses an estimated **~$126K/year** to it. ([Aira analysis of call data][aira])
- A 411 Locals study of 85 businesses found only **37.8%** of calls reach a live person — **24.3% get no answer at all**. ([via Eden][eden])
- **85% of callers who hit voicemail never call back**, and ~80% hang up without leaving a message. ([Aira][aira])
- **Flower shops specifically:** ~**1 in 4** calls goes unanswered during business hours, **two-thirds of voicemail callers just call another florist**, and phone orders are **35–45% of a florist's revenue** — the highest-value channel. A single missed call ≈ **$85**. On Valentine's/Mother's Day a busy shop sees **100+ calls a day**. ([AgentZap florist phone stats][florist])
- **Restaurants** miss **~34–43%** of calls — up to **$27K–$292K/year** depending on volume. ([HungerRush][hunger])
- It's worst exactly when no one can pick up: **40%+ of high-intent inquiries arrive evenings and weekends** (41% of home-services jobs are booked after hours). And speed is everything — answering within 5 minutes makes you **21× more likely** to win the lead. ([Kixie speed-to-lead][kixie])

**The wedge in one sentence:** a flower shop on Mother's Day with three staff and ten ringing lines doesn't need a chatbot — it needs a reliable employee for the *second line*, one that can be tested and trusted with real orders.

## Why this is more than a voice demo

Most hackathon entries demo *one nice conversation*. The hard part of a voice **employee** isn't talking — it's being trustworthy enough to take real orders, remember real constraints, and not do something dumb on call #4,000. SecondLine is built as a **production loop**:

```
 Twilio ──▶ Pipecat pipeline ──▶ typed tools ──▶ business brain (SQLite)
   (phone)   STT→LLM→TTS          (no hallucinated         persistent memory,
                                    business state)         orders, tasks, prefs
                                        │
                                        ▼
                          owner SMS + dashboard (tasks, transcripts)
                                        │
            ┌───────────────────────────┴───────────────────────────┐
            ▼                                                         │
   Cekura / eval harness ──▶ failures ──▶ auto-improve ──▶ patched policy
   (50+ simulated calls)     (bug reports)  (4 fix types)   (re-run, prove ↑)
            └─────────────────────── closed loop ─────────────────────┘
```

The LLM is **never trusted to invent business state**. It can only act through typed tools (`list_bouquets`, `add_to_order`, `reorder_last`, `update_customer_memory`, `send_customer_text`, `escalate_to_owner`, `place_order`, …) that read/write the SQLite **business brain**. That trust boundary is what makes the eval loop meaningful.

## The self-improvement loop (the thing the judges asked for)

> *"the judges want to see great examples of using Cekura to improve voice agent performance"* — hackathon README.

SecondLine treats evaluation as a **product feature**, not a one-off test:

1. **Scenarios** (`eval/scenarios.py`) — new caller, repeat caller, **allergy trap**, noisy/impatient caller, ambiguous date, cancellation, price/policy question, sold-out item, angry-refund (escalation), impossible request, "text me the address", multi-item.
2. **Harness** (`eval/harness.py`) — an LLM plays the caller against the **real agent brain** (`server/agent.py` — the exact code the phone runs). Grades 6 dimensions: **task completion · correct tool use · memory accuracy · escalation behavior · hallucination · latency**.
3. **Auto-improve** (`eval/improve.py`) — each failure becomes one of **four fix types**: a **validation rule** (e.g. the allergen guard), an **escalation rule**, a **prompt patch**, or a **memory rule**. Fixes land in an inspectable `agent_policy.json` the live bot also reads.
4. **Re-run & prove it** — same suite, new policy, **before→after pass rate** on the dashboard. Patches show *why* each change was made, traced to the failing call.

The signature example: the baseline agent will happily add **Lily Elegance** to an order for a caller whose file says *"allergic to lilies."* The eval flags it `SAFETY FAIL` → improve enables `enforce_allergens` → on re-run the same request is **blocked** with a safe alternative. Real diff, not vibes. Cekura is wired in for the live, sponsor-graded version of exactly this loop (see below).

## Repo layout

```
server/        the live voice agent (deploys to Pipecat Cloud)
  bot.py          Pipecat pipeline + Twilio/WebRTC transport (Nemotron default, GPT-4.1 fallback)
  agent.py        SHARED brain: system prompt + tool schemas + dispatch()  ← bot AND eval use this
  backend.py      business brain: SQLite (customers, prefs/allergies, orders, tasks, transcripts)
  policy.py       editable agent policy (the surface the improvement loop patches)
  sms.py          Twilio outbound SMS (owner summaries + "text me the address")
  nemotron_llm.py / nvidia_stt.py   NVIDIA open-model services (from the starter)
eval/          the self-improvement loop
  scenarios.py · harness.py · improve.py · run_eval.py
dashboard/     owner view (FastAPI): tasks, transcripts, before/after pass rate
```

## Quickstart (local, ~2 min)

```bash
cd server
cp .env.example .env          # fill OPENAI_API_KEY + GRADIUM_API_KEY (+ NEMOTRON_* if using NVIDIA)
uv sync
python backend.py --reset     # seed the catalog + repeat customers (Alex is allergic to lilies)
uv run bot.py                 # open http://localhost:7860 and click Connect to talk
```

Run the self-improvement demo (needs `OPENAI_API_KEY`):

```bash
uv run python ../eval/run_eval.py        # baseline → auto-improve → after, prints before/after
python ../dashboard/app.py               # http://localhost:8080 to see it visualized
```

Model shootout (NVIDIA vs OpenAI), the same suite on each:

```bash
EVAL_AGENT_PROVIDER=nemotron uv run python ../eval/run_eval.py
EVAL_AGENT_PROVIDER=gpt      uv run python ../eval/run_eval.py
```

## Go live (phone + Cekura)

Full step-by-step in **[RUNBOOK.md](RUNBOOK.md)**. Short version:

```bash
pc cloud secrets set secondline-secrets --file server/.env
cd server && pc cloud deploy            # agent_name = "secondline"
# point a Twilio number's TwiML Bin at wss://api.pipecat.daily.co/ws/twilio
#   <Parameter name="_pipecatCloudServiceHost" value="secondline.YOUR_ORG"/>
```

**Cekura:** install the plugin and run the live, sponsor-graded loop — SecondLine's eval harness mirrors it locally, and the failure→patch→re-run engine consumes either source.

```
/plugin marketplace add cekura-ai/cekura-skills
/plugin install cekura@cekura-skills
/cekura-report
```

## Demo script (2 minutes)

See **[DEMO.md](DEMO.md)**. The closer: a returning caller says *"Same as last time, but no lilies — my mom's allergic. And text me the pickup address."* SecondLine recalls the last order, **drops/blocks the allergen**, persists the allergy for next time, texts the address, creates the owner task — then we flip to the dashboard and show the **before→after eval jump** that made the agent safe.

---

*Sources:* [Aira — missed-call statistics][aira] · [Eden — voicemail/411 Locals][eden] · [AgentZap — florist phone stats][florist] · [HungerRush — restaurant missed calls][hunger] · [Kixie — speed-to-lead][kixie]

[aira]: https://www.getaira.io/blog/missed-business-calls-statistics
[eden]: https://ringeden.com/blog/how-much-business-do-i-lose-from-voicemail
[florist]: https://agentzap.ai/blog/florist-phone-statistics
[hunger]: https://www.hungerrush.com/restaurant-operations/heres-how-much-revenue-your-restaurant-loses-from-unanswered-phone-calls/
[kixie]: https://www.kixie.com/sales-blog/speed-to-lead-response-time-statistics-that-drive-conversions/
