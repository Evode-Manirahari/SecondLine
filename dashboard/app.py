#!/usr/bin/env python3
#
# SecondLine — owner dashboard + observability.
#
# SPDX-License-Identifier: BSD 2-Clause License
#
"""The owner's view: what the voice agent did, what needs a human, and proof the
agent is getting better.

Reads the same SQLite business brain the live bot writes to, plus the eval
reports from eval/reports/. Run it::

    uv run python dashboard/app.py        # serves http://localhost:8080

Panels:
  * KPIs — calls handled, sales captured, owner tasks, before/after pass rate
  * Owner task queue — escalations, orders, follow-ups (the actionable output)
  * Self-improvement — before vs after pass rate, applied patches, failure cats
  * Recent calls — transcripts + which model + latency
  * Customer memory — what the shop remembers (allergies highlighted)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import backend  # noqa: E402

REPORTS = ROOT / "eval" / "reports"
app = FastAPI(title="SecondLine Dashboard")


@app.get("/api/state")
def state():
    calls = backend.recent_calls(30)
    for c in calls:
        c["transcript"] = backend.transcript_for(c["call_id"])
    eval_data = {}
    latest = REPORTS / "latest.json"
    if latest.exists():
        try:
            eval_data = json.loads(latest.read_text())
        except Exception:
            eval_data = {}
    customers = backend.all_customers()
    tasks = backend.list_tasks(status=None)
    sales = sum(t["details_json"] and json.loads(t["details_json"]).get("total", 0) or 0
                for t in tasks if t["kind"] == "order")
    return JSONResponse({
        "calls": calls,
        "tasks": tasks,
        "customers": customers,
        "eval": eval_data,
        "kpi": {
            "calls_handled": len(calls),
            "open_tasks": len([t for t in tasks if t["status"] == "open"]),
            "sales_captured": round(sales, 2),
            "customers_known": len(customers),
        },
    })


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


HTML = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SecondLine — Owner Dashboard</title>
<style>
:root{--bg:#0b0f14;--card:#141b24;--line:#1f2a36;--ink:#e6edf3;--mut:#8a97a6;
--grn:#34d399;--red:#f87171;--amb:#fbbf24;--acc:#7c9cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{padding:20px 28px;border-bottom:1px solid var(--line);display:flex;
align-items:baseline;gap:14px}
h1{font-size:20px;margin:0;letter-spacing:.3px}
.tag{color:var(--mut);font-size:13px}
.wrap{padding:22px 28px;display:grid;gap:18px;max-width:1200px;margin:0 auto}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
.kpi .n{font-size:28px;font-weight:650}.kpi .l{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.6px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.card h2{font-size:14px;margin:0 0 12px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut)}
.row{display:flex;justify-content:space-between;gap:10px;padding:9px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}
.pill{font-size:11px;padding:2px 8px;border-radius:999px;font-weight:600}
.p-order{background:#10341f;color:var(--grn)}.p-escalation{background:#3a1414;color:var(--red)}
.p-followup{background:#3a2f10;color:var(--amb)}
.bars{display:flex;align-items:flex-end;gap:26px;height:160px;padding:8px 6px}
.bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:8px;justify-content:flex-end;height:100%}
.bar .col{width:64px;border-radius:8px 8px 0 0;transition:height .5s}
.bar .v{font-weight:700;font-size:18px}.bar .lbl{color:var(--mut);font-size:12px}
.before{background:#3a2f10}.after{background:linear-gradient(180deg,#34d399,#0f9b6c)}
.patch{padding:8px 0;border-bottom:1px solid var(--line);font-size:13px}
.patch .t{color:var(--acc);font-weight:600}
.mut{color:var(--mut)}.grn{color:var(--grn)}.red{color:var(--red)}.amb{color:var(--amb)}
.alg{color:var(--red);font-weight:600}
.tx{margin-top:6px;font-size:12px;color:var(--mut);max-height:0;overflow:hidden;transition:max-height .3s}
.call.open .tx{max-height:600px}
.call{padding:9px 0;border-bottom:1px solid var(--line);cursor:pointer}
.tx b{color:var(--ink)}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:7px 6px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.full{grid-column:1/3}
@media(max-width:880px){.grid,.kpis{grid-template-columns:1fr 1fr}.full{grid-column:auto}}
</style></head><body>
<header><h1>🌷 SecondLine</h1>
<span class="tag">Self-improving voice agent for missed calls · owner view · auto-refresh 3s</span></header>
<div class="wrap">
 <div class="kpis" id="kpis"></div>
 <div class="grid">
  <div class="card full">
    <h2>Self-improvement loop — before vs after auto-patch</h2>
    <div id="improve"></div>
  </div>
  <div class="card"><h2>Owner task queue</h2><div id="tasks"></div></div>
  <div class="card"><h2>Recent calls (click to expand)</h2><div id="calls"></div></div>
  <div class="card full"><h2>What the shop remembers</h2><div id="customers"></div></div>
 </div>
</div>
<script>
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function tick(){
 let d; try{ d=await (await fetch('/api/state')).json() }catch(e){return}
 const k=d.kpi;
 document.getElementById('kpis').innerHTML=[
   ['Calls handled',k.calls_handled],['Sales captured','$'+k.sales_captured],
   ['Open owner tasks',k.open_tasks],['Customers remembered',k.customers_known]
 ].map(([l,n])=>`<div class="kpi"><div class="n">${n}</div><div class="l">${l}</div></div>`).join('');

 // self-improvement
 const e=d.eval||{}; const b=e.before, a=e.after;
 let html='';
 if(b&&a){
   const bp=Math.round(b.pass_rate*100), ap=Math.round(a.pass_rate*100);
   const cu=r=>(r.results||[]).filter(x=>(x.failure_reasons||[]).some(s=>s.indexOf('SAFETY FAIL')>=0)).length;
   const ub=(b.unsafe_actions!=null?b.unsafe_actions:cu(b)), ua=(a.unsafe_actions!=null?a.unsafe_actions:cu(a));
   html=`<div style="display:flex;gap:30px;align-items:center;flex-wrap:wrap">
     <div class="bars" style="max-width:240px">
       <div class="bar"><div class="v amb">${bp}%</div><div class="col before" style="height:${Math.max(bp,4)}%"></div><div class="lbl">before</div></div>
       <div class="bar"><div class="v grn">${ap}%</div><div class="col after" style="height:${Math.max(ap,4)}%"></div><div class="lbl">after</div></div>
     </div>
     <div style="flex:1;min-width:260px">
       <div style="font-size:17px;margin-bottom:6px">⚠️ <b>Unsafe actions:</b> <span class="red">${ub}</span> &rarr; <span class="grn">${ua}</span> <span class="mut">(allergen safety violations)</span></div>
       <div class="mut">Agent model: <b>${esc(b.model||'')}</b> · ${b.passed}/${b.total} → ${a.passed}/${a.total} scenarios pass</div>
       <div class="mut">Avg latency: ${b.avg_latency_ms}ms → ${a.avg_latency_ms}ms · score ${b.avg_score}→${a.avg_score}</div>
       <div style="margin-top:10px">${(e.patches||[]).map(p=>`<div class="patch"><span class="t">[${esc(p.type)}]</span> ${esc(p.rationale)}<div class="mut">↳ ${esc(p.source_failure)}</div></div>`).join('')||'<span class=mut>No patches</span>'}</div>
     </div></div>`;
 } else if(e.results){
   const p=Math.round(e.pass_rate*100);
   html=`<div class="mut">Latest run (${esc(e.model||'')}): <b class="grn">${p}%</b> pass (${e.passed}/${e.total}), avg latency ${e.avg_latency_ms}ms.</div>`;
 } else { html='<div class="mut">Run <code>uv run python eval/run_eval.py</code> to populate the self-improvement report.</div>'; }
 document.getElementById('improve').innerHTML=html;

 // tasks
 document.getElementById('tasks').innerHTML=(d.tasks||[]).slice(0,12).map(t=>{
   let det={};try{det=JSON.parse(t.details_json||'{}')}catch(e){}
   return `<div class="row"><div><span class="pill p-${t.kind}">${t.kind}</span> ${esc(t.summary)}</div>
     <div class="mut">${t.status==='open'?'●':'✓'}</div></div>`}).join('')||'<div class=mut>No tasks yet.</div>';

 // calls
 document.getElementById('calls').innerHTML=(d.calls||[]).slice(0,12).map((c,i)=>{
   const tx=(c.transcript||[]).map(x=>`<div><b>${x.role}:</b> ${esc(x.text)}</div>`).join('');
   const oc=c.outcome==='escalated'?'red':(c.outcome==='completed'?'grn':'amb');
   return `<div class="call" onclick="this.classList.toggle('open')">
     <div class="row" style="border:0;padding:4px 0"><div>${esc(c.phone)} <span class="mut">· ${esc(c.model||'')}</span></div>
     <div class="${oc}">${esc(c.outcome||'')} ${c.first_response_ms?('· '+c.first_response_ms+'ms'):''}</div></div>
     <div class="tx">${tx||'<span class=mut>no transcript</span>'}</div></div>`}).join('')||'<div class=mut>No calls yet.</div>';

 // customers
 document.getElementById('customers').innerHTML='<table><tr><th>Phone</th><th>Name</th><th>Allergies</th><th>Last order</th></tr>'+
   (d.customers||[]).map(c=>`<tr><td>${esc(c.phone)}</td><td>${esc(c.name||'')}</td>
   <td class="${c.allergies.length?'alg':''}">${c.allergies.join(', ')||'—'}</td>
   <td class="mut">${c.last_order?esc((c.last_order.items||[]).map(i=>i.quantity+'x '+i.bouquet).join(', ')):'—'}</td></tr>`).join('')+'</table>';
}
tick(); setInterval(tick,3000);
</script></body></html>
"""


if __name__ == "__main__":
    import uvicorn
    backend.seed()  # ensure DB exists
    uvicorn.run(app, host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "8080")))
