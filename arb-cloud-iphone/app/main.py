import os, json, sqlite3, uuid, smtplib, ssl
from email.message import EmailMessage
from contextlib import contextmanager
from pathlib import Path
from decimal import Decimal
import base64
import binascii
import csv
import io
import secrets
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict, StrictBool, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "arbitrage.db"))
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

BANKROLL = 10000.0
STAKE_PER_SET = 500.0
MAX_OPEN_SETS = 5
MAX_OPEN_EXPOSURE = 2500.0
MIN_NET_ROI = 0.015

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS opportunities (
            id TEXT PRIMARY KEY, created_at TEXT, sport TEXT, event TEXT, market TEXT,
            outcomes_json TEXT, sum_inverse REAL, roi REAL, stake_json TEXT, status TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS sets (
            id TEXT PRIMARY KEY, opportunity_id TEXT, created_at TEXT, sport TEXT,
            event TEXT, market TEXT, total_stake REAL, roi REAL, expected_profit REAL,
            status TEXT, actual_pnl REAL, notes TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, subscription_json TEXT UNIQUE
        )""")

app = FastAPI(title="Arbitrage Control API", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.middleware("http")
async def private_access(request, call_next):
    password = os.getenv("ADMIN_PASSWORD", "")
    if password and request.url.path != "/api/health":
        valid = False
        try:
            scheme, token = request.headers.get("authorization", "").split(" ", 1)
            username, supplied = base64.b64decode(token, validate=True).decode("utf-8").split(":", 1)
            valid = (scheme.lower() == "basic"
                     and secrets.compare_digest(username.encode(), os.getenv("ADMIN_USERNAME", "admin").encode())
                     and secrets.compare_digest(supplied.encode(), password.encode()))
        except (ValueError, UnicodeError, binascii.Error):
            pass
        if not valid:
            return JSONResponse({"detail": "Authentication required."}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Arbitrage Control"'})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    return response

class InputModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, str_strip_whitespace=True)

class Outcome(InputModel):
    selection: str = Field(min_length=1, max_length=200)
    bookmaker: str = Field(min_length=1, max_length=200)
    odds: float = Field(gt=1.0)
    commission_pct: float = Field(default=0.0, ge=0, lt=100)

class Candidate(InputModel):
    sport: str = Field(min_length=1, max_length=100)
    event: str = Field(min_length=1, max_length=300)
    market: str = Field(min_length=1, max_length=300)
    # One price for each mutually exclusive outcome. This starter API accepts two or three legs.
    outcomes: List[Outcome] = Field(min_length=2, max_length=3)
    total_stake: float = Field(default=500, gt=0, le=500)

    @field_validator("total_stake")
    @classmethod
    def whole_cents(cls, value):
        if Decimal(str(value)) % Decimal("0.01"):
            raise ValueError("Stake must be a whole number of cents.")
        return value

    @model_validator(mode="after")
    def unique_selections(self):
        selections = [o.selection.casefold() for o in self.outcomes]
        if len(set(selections)) != len(selections):
            raise ValueError("Each outcome must have a different selection.")
        return self

class OpenSet(InputModel):
    opportunity_id: str
    manual_confirmation: StrictBool
    notes: str = ""

class SettleSet(InputModel):
    actual_pnl: float
    notes: str = ""

class PushSub(BaseModel):
    subscription: dict

def net_decimal_odds(outcome: Outcome):
    # Approximate commission adjustment on net winnings; not suitable for all exchange markets.
    c = outcome.commission_pct / 100.0
    return 1.0 + (outcome.odds - 1.0) * (1.0 - c)

def calculate(candidate: Candidate):
    adjusted = [net_decimal_odds(o) for o in candidate.outcomes]
    inv = [1.0 / o for o in adjusted]
    s = sum(inv)
    roi = (1.0 / s) - 1.0
    stakes = [candidate.total_stake * x / s for x in inv]
    # round to cents and leave rounding residual on last leg
    rounded = [round(x, 2) for x in stakes]
    rounded[-1] = round(candidate.total_stake - sum(rounded[:-1]), 2)
    payout = min(rounded[i] * adjusted[i] for i in range(len(rounded)))
    profit = payout - candidate.total_stake
    return {"sum_inverse": s, "roi": roi, "net_roi": profit / candidate.total_stake, "stakes": rounded,
            "equalised_payout": payout, "profit": profit, "adjusted_odds": adjusted}

def send_email(subject, body):
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    to = os.getenv("ALERT_EMAIL")
    if not all([host, user, password, to]):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.getenv("SMTP_FROM", user)
    msg["To"] = to
    msg.set_content(body)
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(user, password)
        server.send_message(msg)
    return True

def send_push(payload):
    # Push delivery is activated after VAPID keys are configured and a device subscribes.
    try:
        from pywebpush import webpush, WebPushException
        pub = os.getenv("VAPID_PUBLIC_KEY")
        priv = os.getenv("VAPID_PRIVATE_KEY")
        subject = os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")
        if not pub or not priv:
            return 0
        with db() as con:
            rows = con.execute("SELECT subscription_json FROM push_subscriptions").fetchall()
        sent = 0
        for row in rows:
            try:
                webpush(json.loads(row["subscription_json"]), json.dumps(payload),
                        vapid_private_key=priv,
                        vapid_claims={"sub": subject})
                sent += 1
            except Exception:
                pass
        return sent
    except Exception:
        return 0

@app.on_event("startup")
def startup():
    init_db()

@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")

@app.get("/api/health")
def health():
    return {"ok": True, "service": "arb-control-api", "mode": "manual-confirmation-only"}

@app.get("/api/config")
def config():
    return {"bankroll": BANKROLL, "stake_per_set": STAKE_PER_SET,
            "max_open_sets": MAX_OPEN_SETS, "max_open_exposure": MAX_OPEN_EXPOSURE,
            "min_net_roi": MIN_NET_ROI,
            "sports": ["Soccer", "Tennis", "Basketball / NBA", "NRL", "AFL", "Cricket"],
            "horse_racing": "planned phase 2"}

@app.post("/api/scan")
def scan(candidate: Candidate):
    result = calculate(candidate)
    if result["net_roi"] < MIN_NET_ROI or min(result["stakes"]) <= 0:
        return {"qualified": False, "reason": "Below 1.5% minimum net ROI",
                **result, "candidate": candidate.model_dump()}
    opp_id = str(uuid.uuid4())
    data = candidate.model_dump()
    with db() as con:
        con.execute("""INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (opp_id, now(), candidate.sport, candidate.event, candidate.market,
             json.dumps(data["outcomes"]), result["sum_inverse"], result["roi"],
             json.dumps(result["stakes"]), "Candidate"))
    body = (f"{candidate.sport}: {candidate.event}\n{candidate.market}\n"
            f"Net theoretical ROI: {result['roi']:.2%}\n"
            f"Total stake: ${candidate.total_stake:.2f}\n"
            + "\n".join(f"{o.selection} / {o.bookmaker}: ${result['stakes'][i]:.2f} at {o.odds}"
                        for i,o in enumerate(candidate.outcomes))
            + "\nManual verification required. No bets have been placed.")
    email_sent = False
    try: email_sent = send_email("Arbitrage candidate detected", body)
    except Exception: pass
    push_count = send_push({"title": "Arbitrage candidate", "body": f"{candidate.event} • {result['roi']:.2%} theoretical ROI"})
    return {"qualified": True, "opportunity_id": opp_id, **result,
            "email_sent": email_sent, "push_devices_notified": push_count,
            "candidate": data}

@app.get("/api/opportunities")
def opportunities():
    with db() as con:
        rows = con.execute("SELECT * FROM opportunities ORDER BY created_at DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

@app.get("/api/sets")
def sets():
    with db() as con:
        rows = con.execute("SELECT * FROM sets ORDER BY created_at DESC LIMIT 100").fetchall()
        active = con.execute("SELECT COUNT(*) n, COALESCE(SUM(total_stake),0) exposure FROM sets WHERE status='Open'").fetchone()
    return {"items": [dict(r) for r in rows], "open_sets": active["n"],
            "open_exposure": active["exposure"],
            "available_slots": max(0, MAX_OPEN_SETS-active["n"]),
            "remaining_exposure": max(0, MAX_OPEN_EXPOSURE-active["exposure"])}

@app.post("/api/sets/open")
def open_set(req: OpenSet):
    if not req.manual_confirmation:
        raise HTTPException(400, "Manual confirmation is required.")
    with db() as con:
        # Serialize the capacity check and insertion across concurrent requests.
        con.execute("BEGIN IMMEDIATE")
        opp = con.execute("SELECT * FROM opportunities WHERE id=?", (req.opportunity_id,)).fetchone()
        if not opp: raise HTTPException(404, "Opportunity not found.")
        if opp["status"] != "Candidate":
            raise HTTPException(409, "Opportunity has already been opened.")
        stakes = json.loads(opp["stake_json"])
        total_stake = round(sum(stakes), 2)
        outcomes = [Outcome(**item) for item in json.loads(opp["outcomes_json"])]
        profit = min(stake * net_decimal_odds(outcome)
                     for stake, outcome in zip(stakes, outcomes)) - total_stake
        active = con.execute("SELECT COUNT(*) n, COALESCE(SUM(total_stake),0) exposure FROM sets WHERE status='Open'").fetchone()
        if active["n"] >= MAX_OPEN_SETS: raise HTTPException(409, "Five simultaneous sets already open.")
        if active["exposure"] + total_stake > MAX_OPEN_EXPOSURE:
            raise HTTPException(409, "Open exposure limit would be exceeded.")
        set_id = str(uuid.uuid4())
        con.execute("INSERT INTO sets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (set_id, req.opportunity_id, now(), opp["sport"], opp["event"], opp["market"],
             total_stake, opp["roi"], profit, "Open", None, req.notes))
        con.execute("UPDATE opportunities SET status='Opened' WHERE id=?", (req.opportunity_id,))
    return {"ok": True, "set_id": set_id, "message": "Set recorded as open. This did not place any bookmaker bets."}

@app.get("/api/reports/summary")
def report_summary():
    with db() as con:
        row = con.execute("""SELECT COUNT(*) total_sets,
            COALESCE(SUM(CASE WHEN status='Open' THEN total_stake ELSE 0 END),0) open_exposure,
            COALESCE(SUM(CASE WHEN status='Settled' THEN actual_pnl ELSE 0 END),0) realised_pnl,
            COALESCE(SUM(CASE WHEN status='Settled' THEN 1 ELSE 0 END),0) settled_sets
            FROM sets""").fetchone()
    return dict(row)

@app.get("/api/reports/sets.csv")
def export_sets():
    with db() as con:
        rows = con.execute("SELECT * FROM sets ORDER BY created_at, id").fetchall()
        columns = [column[1] for column in con.execute("PRAGMA table_info(sets)")]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        # Prevent user-entered text from becoming a spreadsheet formula.
        writer.writerow([("'" + value if isinstance(value, str)
                          and value.lstrip().startswith(("=", "+", "-", "@")) else value)
                         for value in row])
    return Response(output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="arbitrage-sets.csv"'})

@app.post("/api/sets/{set_id}/settle")
def settle(set_id: str, req: SettleSet):
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM sets WHERE id=?", (set_id,)).fetchone()
        if not row: raise HTTPException(404, "Set not found.")
        if row["status"] != "Open": raise HTTPException(409, "Set is not open.")
        con.execute("UPDATE sets SET status='Settled', actual_pnl=?, notes=? WHERE id=?",
                    (req.actual_pnl, req.notes, set_id))
    return {"ok": True}

@app.post("/api/push/subscribe")
def subscribe(req: PushSub):
    with db() as con:
        con.execute("INSERT OR IGNORE INTO push_subscriptions(subscription_json) VALUES (?)",
                    (json.dumps(req.subscription),))
    return {"ok": True}

@app.get("/api/push/public-key")
def push_key():
    return {"public_key": os.getenv("VAPID_PUBLIC_KEY", "")}

@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

