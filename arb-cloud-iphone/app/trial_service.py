"""Dedicated read-only 350-credit trial monitor.

The scanner is intentionally disabled until durable DB and provider secrets
are configured and integration tests verify real provider request costs.
"""
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from scripts.trial_credit_ledger import connect, monitor

app = FastAPI(title="Australian Arbitrage Trial Monitor")

@app.get("/health")
def health():
    return {"status": "ok", "scanner_enabled": False}

@app.get("/api/trial/status")
def status():
    if not os.environ.get("TRIAL_DATABASE_URL"):
        return {"status": "setup_required", "credits_remaining": 350,
                "scanner_enabled": False, "note": "Persistent database connection not configured"}
    try:
        with connect() as con:
            result = monitor(con)
        result["scanner_enabled"] = False
        return result
    except Exception:
        return {"status": "database_unavailable", "scanner_enabled": False,
                "note": "Monitoring unavailable; scanning is disabled"}

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Arbitrage Trial</title>
<style>body{background:#09172b;color:#f1f6ff;font:16px system-ui;margin:0;padding:24px}
main{max-width:580px;margin:auto}article{background:#132842;border:1px solid #2d4868;border-radius:16px;padding:20px;margin:14px 0}
h1{font-size:24px}h2{font-size:16px;color:#9dc9f4}strong{font-size:28px}small{color:#b5c7db}button{background:#2574d9;color:white;border:0;border-radius:10px;padding:12px}</style></head><body><main>
<h1>Australian Arbitrage Control</h1><small>24-hour trial monitor • 350-credit hard limit</small>
<article><h2>Scanner status</h2><strong id="status">Loading</strong><p id="note"></p></article>
<article><h2>Credits remaining</h2><strong id="credits">—</strong><p>Maximum 350 credits</p></article>
<article><h2>Scans / opportunities</h2><strong id="scans">—</strong><p id="opps"></p></article>
<article><h2>Trial window</h2><p id="window">Not started</p><small>Updates every 30 seconds while this page is open</small></article>
<button onclick="refresh()">Refresh now</button>
<script>
async function refresh(){try{const r=await fetch('/api/trial/status',{cache:'no-store'});const d=await r.json();
document.getElementById('status').textContent=d.status||'unknown';
document.getElementById('credits').textContent=d.credits_remaining??'—';
document.getElementById('scans').textContent=d.scan_count??0;
document.getElementById('opps').textContent='Qualifying opportunities: '+(d.qualified_count??0);
document.getElementById('note').textContent=d.note||'Automatic scanning is not yet enabled';
document.getElementById('window').textContent=d.started_at?new Date(d.started_at).toLocaleString()+' — '+new Date(d.ends_at).toLocaleString():'Not started';
}catch(e){document.getElementById('status').textContent='Connection unavailable'}}
refresh();setInterval(refresh,30000);
</script></main></body></html>"""
