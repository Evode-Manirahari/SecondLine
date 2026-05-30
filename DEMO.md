# SecondLine — 2-minute demo script

Goal: show a **production loop**, not a chatbot. Three beats: it works → it's safe & remembers → it makes itself better.

## Setup (before judges arrive)
```bash
cd server && python backend.py --reset      # clean seeded brain
uv run python ../eval/run_eval.py           # generates before/after report for the dashboard
python ../dashboard/app.py                  # leave running on :8080 on a second screen
uv run bot.py                               # or your deployed Twilio number
```
Have the dashboard open on one screen, the phone/WebRTC client on the other.

## Beat 1 — "It takes a real order" (35s)
Call in as a **new** customer:
> "Hi, do you have anything good for a birthday? … Great, the Birthday Brights. Deliver to Casey at 300 Elm Street this Friday."

Point out: it recommends from the *real* catalog, captures delivery one question at a time, reads back the order, and **places it**. Flip to the dashboard — a new **owner task** appeared and (if Twilio is live) the owner just got an **SMS summary**.

## Beat 2 — THE CLOSER: memory + safety (45s)
Call back as the **returning** customer `+14155551234` (Alex), whose file says *allergic to lilies*:
> "Same as last time, but no lilies — my mom's allergic. And can you text me the pickup address?"

SecondLine:
1. **`reorder_last`** → recalls the rose romance from last time (no need to re-ask).
2. **`update_customer_memory`** → persists the lily allergy for *next* time.
3. **Allergen guard** → if you'd pushed a lily bouquet, `add_to_order` would **refuse** and offer a safe swap.
4. **`send_customer_text`** → texts the pickup address.
5. **`place_order`** → owner task + SMS.

Say the line out loud to judges: *"It didn't just talk — it remembered a safety constraint and acted on it through typed tools. The model can't invent an order."*

## Beat 3 — "It improves itself" (40s)
Switch to the dashboard's **Self-improvement** panel:
- **Before**: baseline agent fails the *allergy trap* — it adds Lily Elegance to an allergic caller (`SAFETY FAIL`), and misses an escalation.
- **One click of the loop**: each failure became a concrete patch — a **validation rule** (allergen guard ON), an **escalation rule**, a **prompt patch** — written to `agent_policy.json`, each traced to the failing call.
- **After**: pass rate jumps; the same allergy call is now **blocked**.

Closer line: *"This is the Cekura loop built into the product. Failures become fixes automatically, and we can prove the agent got safer — before/after, on real transcripts."*

## If asked "NVIDIA?"
```bash
EVAL_AGENT_PROVIDER=nemotron uv run python ../eval/run_eval.py
```
Same suite, Nemotron-3-Super as the agent — show the score side-by-side with GPT-4.1. The bot defaults to Nemotron (open weights) with GPT-4.1 as fallback.

## One-liner pitch
*"I didn't build a chatbot. I built a voice employee for the second line — one that remembers, acts through typed tools, and tests and improves itself on every call."*
