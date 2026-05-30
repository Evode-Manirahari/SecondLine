# SecondLine — runbook (local → live phone → Cekura)

## 0. Prereqs
- Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and the keys provided on-site:
  `OPENAI_API_KEY`, `GRADIUM_API_KEY`, Twilio creds, and (for NVIDIA) `NEMOTRON_LLM_URL` / `NVIDIA_ASR_URL`.

## 1. Local dev loop (fastest iteration)
```bash
cd server
cp .env.example .env          # fill in keys
uv sync
python backend.py --reset     # seed catalog + repeat customers
uv run bot.py                 # http://localhost:7860 → Connect → talk in the browser
```
Force a model: `LLM_PROVIDER=gpt uv run bot.py` or `LLM_PROVIDER=nemotron uv run bot.py`.
(Default: Nemotron if `NEMOTRON_LLM_URL` is set, otherwise GPT-4.1.)

NVIDIA endpoints (from the hackathon README):
```bash
export NVIDIA_ASR_URL=ws://44.241.251.184:8080
export NEMOTRON_LLM_URL=http://nemotron-fleet-alb-1322439314.us-west-2.elb.amazonaws.com/v1
export NEMOTRON_LLM_MODEL=nvidia/nemotron-3-super
```

## 2. Self-improvement eval
```bash
uv run python ../eval/run_eval.py                 # baseline → improve → after
uv run python ../eval/run_eval.py --only allergy_trap killer_reorder_no_lilies
EVAL_AGENT_PROVIDER=nemotron uv run python ../eval/run_eval.py   # model shootout
```
Reports land in `eval/reports/` (`report_before.json`, `report_after.json`, `patches.json`, `latest.json`).

## 3. Owner dashboard
```bash
python ../dashboard/app.py        # http://localhost:8080  (set PORT to change)
```
Reads the same `secondline.db` the bot writes + the eval reports. Auto-refreshes every 3s.

## 4. Deploy to Pipecat Cloud + Twilio
```bash
# from repo root
uv tool install pipecat-ai-cli
pc cloud auth login
pc cloud organizations list                      # note YOUR_ORG

cd server
pc cloud secrets set secondline-secrets --file .env
pc cloud deploy                                  # builds Dockerfile, agent_name=secondline
```
Twilio TwiML Bin (attach to your number's Voice config):
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://api.pipecat.daily.co/ws/twilio">
      <Parameter name="_pipecatCloudServiceHost" value="secondline.YOUR_ORG"/>
    </Stream>
  </Connect>
</Response>
```
Then **dial the number**. Caller ID drives the returning-customer memory. To demo the killer flow from your own phone, add your number to `server/backend.py` `SEED_CUSTOMERS` (with `["lilies"]` allergy + a last order) and `python backend.py --reset` before deploy.

## 5. Cekura (sponsor-graded live loop)
```bash
/plugin marketplace add cekura-ai/cekura-skills
/plugin install cekura@cekura-skills
/cekura-report                # select Pipecat as the provider; point at your agent
```
Cekura runs real scenarios against the live agent and returns transcripts + scores. Feed its
failures into `eval/improve.py` the same way the local harness does — the four fix types and the
`agent_policy.json` patch surface are identical. Show the before/after in Cekura *and* on the dashboard.

## Notes / gotchas
- **Owner SMS** needs `TWILIO_FROM_NUMBER` + `OWNER_PHONE_NUMBER`. Without them, sends are logged as
  `simulated` in the DB (demo still works; dashboard still shows the task).
- **Production policy** ships safe: `server/agent_policy.json` has `enforce_allergens` and
  `low_confidence_escalation` ON. The eval's `full` run temporarily resets to the v1 baseline to
  demonstrate before/after, then re-applies the patches. Re-run `python -c "import policy;policy.reset_policy()"`
  only if you want to force the unsafe baseline on a live call (you don't, for the demo).
- **DB location** is `server/secondline.db` (override with `SECONDLINE_DB`). The dashboard and bot
  must point at the same file to share state.
- First `uv run bot.py` downloads VAD + turn-detection models (~20s).
