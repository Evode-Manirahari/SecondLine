#!/usr/bin/env python3
#
# SecondLine — eval runner / self-improvement demo driver.
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""Run the SecondLine self-improvement loop end to end.

    uv run python run_eval.py                 # full: baseline -> improve -> after
    uv run python run_eval.py --mode baseline # just score the current policy
    uv run python run_eval.py --only allergy_trap killer_reorder_no_lilies
    EVAL_AGENT_PROVIDER=nemotron uv run python run_eval.py   # NVIDIA model shootout

Writes JSON reports to eval/reports/ which the owner dashboard reads to show
pass rate, latency, failure categories, and the before/after improvement.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "server"))

import backend  # noqa: E402
import policy as policy_mod  # noqa: E402
import harness  # noqa: E402
import improve  # noqa: E402
from scenarios import SCENARIOS, by_id  # noqa: E402

REPORTS = HERE / "reports"
REPORTS.mkdir(exist_ok=True)


async def run_suite(scenarios: list[dict], label: str) -> dict:
    client, model = harness._agent_client_and_model()
    print(f"\n=== Running suite '{label}' on agent model: {model} "
          f"(policy v{policy_mod.load_policy().get('version')}) ===")
    results = []
    for s in scenarios:
        # Deterministic isolation: each scenario starts from the seeded brain.
        backend.seed(reset=True)
        t0 = time.time()
        call = await harness.run_call(s, client, model)
        graded = harness.grade(s, call)
        graded["wall_s"] = round(time.time() - t0, 1)
        graded["transcript"] = call.transcript
        results.append(graded)
        mark = "PASS" if graded["passed"] else "FAIL"
        fails = "" if graded["passed"] else "  -> " + "; ".join(graded["failure_reasons"])
        print(f"  [{mark}] {s['id']:28} score={graded['score']:.2f} "
              f"lat={graded['avg_latency_ms']}ms{fails}")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    by_dim = {d: sum(1 for r in results if r["dimensions"].get(d)) for d in harness.DIMENSIONS}
    cat_fail: dict[str, int] = {}
    for r in results:
        if not r["passed"]:
            cat_fail[r["category"]] = cat_fail.get(r["category"], 0) + 1
    lat = [r["avg_latency_ms"] for r in results if r["avg_latency_ms"]]
    # Safety is the metric that matters most: count unsafe actions (allergen
    # violations) and pass-rate on safety-critical scenarios.
    unsafe = sum(1 for r in results if any("SAFETY FAIL" in x for x in r["failure_reasons"]))
    safety = [r for r in results if r["category"] in ("safety", "memory_safety")]
    safety_passed = sum(1 for r in safety if r["passed"])
    report = {
        "label": label, "model": model, "policy_version": policy_mod.load_policy().get("version"),
        "pass_rate": round(passed / total, 3) if total else 0,
        "passed": passed, "total": total,
        "avg_score": round(sum(r["score"] for r in results) / total, 3) if total else 0,
        "avg_latency_ms": int(sum(lat) / len(lat)) if lat else 0,
        "unsafe_actions": unsafe,
        "safety_passed": safety_passed, "safety_total": len(safety),
        "dimension_pass": by_dim, "failures_by_category": cat_fail,
        "results": results,
    }
    print(f"  --> {passed}/{total} passed ({report['pass_rate']*100:.0f}%), "
          f"avg score {report['avg_score']:.2f}, avg latency {report['avg_latency_ms']}ms")
    return report


def _select(only: list[str] | None) -> list[dict]:
    if not only:
        return SCENARIOS
    out = [by_id(x) for x in only]
    missing = [x for x, s in zip(only, out) if s is None]
    if missing:
        sys.exit(f"Unknown scenario id(s): {missing}")
    return out  # type: ignore


async def main_async(args):
    if not harness.aux_available():
        sys.exit("Set ANTHROPIC_API_KEY (Claude — default) or OPENAI_API_KEY for the eval "
                 "caller-simulator + judge.")
    scenarios = _select(args.only)

    if args.mode == "baseline":
        rep = await run_suite(scenarios, "baseline")
        (REPORTS / "latest.json").write_text(json.dumps(rep, indent=2))
        return

    # full self-improvement demo
    policy_mod.reset_policy()
    print("Policy reset to v1 baseline (allergen guard OFF, low-confidence escalation OFF).")
    before = await run_suite(scenarios, "before")
    (REPORTS / "report_before.json").write_text(json.dumps(before, indent=2))

    print("\n=== Auto-improvement: turning failures into fixes ===")
    patches = improve.propose_patches(before)
    print(improve.summarize(patches))
    if patches:
        improve.apply_patches(patches)
    (REPORTS / "patches.json").write_text(json.dumps(patches, indent=2))

    after = await run_suite(scenarios, "after")
    (REPORTS / "report_after.json").write_text(json.dumps(after, indent=2))

    combined = {"before": before, "after": after, "patches": patches,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    (REPORTS / "latest.json").write_text(json.dumps(combined, indent=2))

    print("\n" + "=" * 60)
    print(f"  SELF-IMPROVEMENT RESULT  ({before['model']})")
    print("=" * 60)
    print(f"  Pass rate:        {before['pass_rate']*100:>5.0f}%  ->  {after['pass_rate']*100:>5.0f}%   "
          f"({before['passed']}/{before['total']} -> {after['passed']}/{after['total']})")
    print(f"  UNSAFE actions:   {before['unsafe_actions']:>5}   ->  {after['unsafe_actions']:>5}     "
          f"(allergen/safety violations)")
    print(f"  Safety scenarios: {before['safety_passed']}/{before['safety_total']}    ->  "
          f"{after['safety_passed']}/{after['safety_total']}")
    print(f"  Avg score:        {before['avg_score']:>5.2f}  ->  {after['avg_score']:>5.2f}")
    print(f"  Patches applied:  {len(patches)}")
    print("=" * 60)
    print("Reports written to eval/reports/. Run the dashboard to visualize.")


def main():
    ap = argparse.ArgumentParser(description="SecondLine self-improvement eval runner")
    ap.add_argument("--mode", choices=["full", "baseline"], default="full")
    ap.add_argument("--only", nargs="*", help="run only these scenario ids")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
