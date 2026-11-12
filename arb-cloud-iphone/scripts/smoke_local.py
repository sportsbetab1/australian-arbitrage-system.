"""Start an isolated localhost server and exercise a fictional position."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx


def run():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="arbitrage-smoke-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        setup_token, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        env = dict(os.environ, DB_PATH=str(Path(directory) / "smoke.sqlite"),
                   BACKUP_DIR=str(Path(directory) / "backups"), AUTH_COOKIE_SECURE="false",
                   AUTH_SETUP_TOKEN=setup_token, ODDS_API_KEY="", VAPID_PUBLIC_KEY="",
                   VAPID_PRIVATE_KEY="", SMTP_HOST="", SMTP_PASSWORD="")
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                    "--port", str(port), "--no-proxy-headers"], cwd=root, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=origin, headers={"Origin": origin}, trust_env=False, timeout=10) as client:
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Isolated server exited before readiness.")
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Isolated server did not become ready.")
                    time.sleep(0.1)

                def post(path, data):
                    response = client.post(path, json=data)
                    if response.status_code != 200:
                        raise RuntimeError(f"Smoke request failed: {path} (HTTP {response.status_code}).")
                    return response.json()

                if client.get("/api/accounts").status_code != 401:
                    raise RuntimeError("Private account route was accessible without sign-in.")
                post("/api/auth/setup", {"setup_token": setup_token, "hunta_password": password, "jay_password": secrets.token_urlsafe(32)})
                post("/api/auth/login", {"username": "hunta", "password": password})
                accounts = client.get("/api/accounts").json()
                selected = [next(a for a in accounts if a["owner"] == owner and a["bookmaker"] == "Sportsbet")
                            for owner in ("Hunta", "Jay")]
                for account in selected:
                    post(f"/api/accounts/{account['id']}/opening-balance", {"amount": 100})
                candidate = {"sport": "Fictional", "event": "Smoke A v B", "market": "Winner", "total_stake": 0.03,
                             "outcomes": [{"selection": s, "bookmaker": "Sportsbet", "odds": 2.1} for s in ("A", "B")]}
                if post("/api/scan", candidate)["qualified"]:
                    raise RuntimeError("Rounded-stake disqualification failed.")
                position = post("/api/positions/open", {
                    "sport": "Fictional", "event": "Smoke A v B", "market": "Winner",
                    "event_id": "smoke", "market_id": "winner", "possible_outcomes": ["a", "b"],
                    "market_rules_verified": True, "manual_confirmation": True,
                    "legs": [{"account_id": a["id"], "event_id": "smoke", "market_id": "winner",
                              "outcome_id": outcome, "selection": outcome.upper(), "odds": 2.1,
                              "stake": 100, "manually_accepted": True} for a, outcome in zip(selected, ("a", "b"))]})
                saved = next(p for p in client.get("/api/positions").json() if p["id"] == position["position_id"])
                for leg, credit in zip(saved["legs"], (210, 0)):
                    post(f"/api/positions/{saved['id']}/legs/{leg['id']}/settle", {"actual_return": credit})
                report = client.get("/api/reports/summary").json()
                if report["positions"]["net_realised_pnl"] != 10:
                    raise RuntimeError("Fictional settlement P/L did not match.")
                post("/api/backups/create", {})
                post("/api/auth/logout", {})
                if client.get("/api/accounts").status_code != 401:
                    raise RuntimeError("Logout did not revoke access.")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print("PASS: isolated startup, authentication, rounded scan, fictional position/settlement, reports, backup and logout.")


if __name__ == "__main__":
    run()
