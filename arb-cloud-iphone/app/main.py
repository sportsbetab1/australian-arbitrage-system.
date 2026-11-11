import os
import json
import uuid
import csv
import io
import re
import smtplib
import ssl
import hashlib
import hmac
import ipaddress
import secrets
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from typing import List, Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.database import get_db, init_db, migrate_position_schema
from app.odds import OddsProviderError, build_h2h_candidates, fetch_json

# ============================================================
# SYSTEM SETTINGS
# ============================================================

STARTING_BANKROLL = 10000.00
HUNTA_ALLOCATION = 4500.00
JAY_ALLOCATION = 4500.00
SETTLEMENT_BUFFER_LIMIT = 1000.00

MAX_EXPOSURE_PERCENT = 0.75
PROTECTED_RESERVE_PERCENT = 0.25
MIN_NET_ROI = 0.015

REINVESTMENT_PERCENT = 0.75
WITHDRAWAL_PERCENT = 0.25

BUFFER_DEADLINE_HOURS = 24
BOOKMAKERS = [
    "Sportsbet",
    "Betfair",
    "PointsBet",
    "Unibet",
    "Dabble",
    "Picklebet",
    "Ladbrokes",
]

MAX_EXPOSURE = round(
    STARTING_BANKROLL * MAX_EXPOSURE_PERCENT, 2
)


def now():
    return datetime.now(timezone.utc)


def now_text():
    return now().isoformat()


def db():
    return get_db()


# ============================================================
# DATABASE INITIALISATION
# ============================================================
def initialise_system():
    init_db()
    migrate_position_schema()

    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS system_config (
            setting TEXT PRIMARY KEY,
            value TEXT NOT NULL        );

        CREATE TABLE IF NOT EXISTS account_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            transaction_type TEXT NOT NULL,
            amount REAL NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reference_id INTEGER,
            FOREIGN KEY(account_id) REFERENCES accounts(id)
        );

        CREATE INDEX IF NOT EXISTS idx_position_legs_position
        ON position_legs(position_id);

        CREATE INDEX IF NOT EXISTS idx_positions_status
        ON positions(status);

        CREATE INDEX IF NOT EXISTS idx_buffer_status
        ON buffer_ledger(status);
        """)

        transaction_columns = {
            row["name"]
            for row in con.execute("PRAGMA table_info(account_transactions)")
        }
        if "reference_id" not in transaction_columns:
            con.execute(
                "ALTER TABLE account_transactions ADD COLUMN reference_id INTEGER"
            )
        con.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_account_transactions_reference
            ON account_transactions(account_id, transaction_type, reference_id)
            WHERE reference_id IS NOT NULL
        """)

        settings = {
            "starting_bankroll": STARTING_BANKROLL,
            "hunta_allocation": HUNTA_ALLOCATION,
            "jay_allocation": JAY_ALLOCATION,
            "settlement_buffer_limit": SETTLEMENT_BUFFER_LIMIT,
            "max_exposure_percent": MAX_EXPOSURE_PERCENT,
            "protected_reserve_percent": PROTECTED_RESERVE_PERCENT,
            "minimum_net_roi": MIN_NET_ROI,
            "reinvestment_percent": REINVESTMENT_PERCENT,
            "withdrawal_percent": WITHDRAWAL_PERCENT,
        }

        for key, value in settings.items():
            con.execute(
                """
                INSERT OR IGNORE INTO system_config(setting, value)
                VALUES (?, ?)
                """,
                (key, str(value)),
            )


# ============================================================
# API AND MODELS
# ============================================================

app = FastAPI(
    title="Australian Arbitrage Control API",
    version="1.0.0",
    description="Manual-confirmation arbitrage control system",
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)

AUTH_COOKIE_NAME = "arb_session"
AUTH_SESSION_HOURS = 12
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "true").lower() == "true"
AUTH_PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/setup",
    "/api/auth/login",
}


@app.middleware("http")
async def require_authenticated_api(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        from urllib.parse import urlsplit

        origin = request.headers.get("origin")
        request_host = request.headers.get("host", "").lower()
        forwarded_scheme = request.headers.get("x-forwarded-proto", "")
        expected_scheme = (
            forwarded_scheme.split(",")[0].strip().lower()
            if forwarded_scheme else request.url.scheme.lower()
        )
        parsed_origin = urlsplit(origin) if origin else None
        if (
            parsed_origin is None
            or parsed_origin.scheme.lower() != expected_scheme
            or parsed_origin.netloc.lower() != request_host
            or parsed_origin.path not in ("", "/")
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            return JSONResponse(
                {"detail": "Request origin is not allowed."},
                status_code=403,
            )

    if not path.startswith("/api/") or path in AUTH_PUBLIC_PATHS:
        return await call_next(request)

    token = request.cookies.get(AUTH_COOKIE_NAME)
    user = get_authenticated_user(token) if token else None
    if user is None:
        response = JSONResponse(
            {"detail": "Please sign in to use the arbitrage dashboard."},
            status_code=401,
        )
        response.delete_cookie(
            AUTH_COOKIE_NAME,
            path="/",
            secure=AUTH_COOKIE_SECURE,
            httponly=True,
            samesite="strict",
        )
        return response

    request.state.auth_user = user
    return await call_next(request)


class Outcome(BaseModel):
    selection: str
    bookmaker: str
    odds: float = Field(gt=1.0)
    commission_pct: float = Field(default=0, ge=0, lt=100)


class Candidate(BaseModel):
    sport: str
    event: str
    market: str
    outcomes: List[Outcome] = Field(min_length=2, max_length=3)
    total_stake: float = Field(gt=0)


class PositionLeg(BaseModel):
    account_id: int
    event_id: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    outcome_id: str = Field(min_length=1)
    selection: str = Field(min_length=1)
    odds: float = Field(gt=1.0)
    stake: float = Field(gt=0)
    commission_pct: float = Field(default=0, ge=0, lt=100)
    manually_accepted: bool = False


class OpenPosition(BaseModel):
    sport: str
    event: str
    market: str
    event_id: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    market_type: Literal["binary"] = "binary"
    possible_outcomes: List[str] = Field(min_length=2, max_length=2)
    market_rules_verified: bool
    legs: List[PositionLeg] = Field(min_length=2, max_length=2)
    manual_confirmation: bool
    notes: str = ""


class RecordHedge(BaseModel):
    account_id: int
    outcome_id: str = Field(min_length=1)
    selection: str = Field(min_length=1)
    odds: float = Field(gt=1.0)
    stake: float = Field(gt=0)
    manual_confirmation: bool
    manually_accepted: bool
    notes: str = Field(default="", max_length=500)


class SettleLeg(BaseModel):
    actual_return: float = Field(ge=0)
    notes: str = ""


class BufferCommit(BaseModel):
    position_id: int | None = None
    amount: float = Field(gt=0)
    description: str = ""


class BufferResolve(BaseModel):
    notes: str = ""


class OpeningBalance(BaseModel):
    amount: float = Field(ge=0)


class AccountTransaction(BaseModel):
    transaction_type: Literal["deposit", "withdrawal"]
    amount: float = Field(gt=0)
    description: str = Field(min_length=1, max_length=240)


class PushSub(BaseModel):
    subscription: dict

class AuthSetup(BaseModel):
    setup_token: str = Field(min_length=1)
    hunta_password: str = Field(min_length=12, max_length=256)
    jay_password: str = Field(min_length=12, max_length=256)


class AuthLogin(BaseModel):
    username: Literal["hunta", "jay"]
    password: str = Field(min_length=1, max_length=256)


password_hasher = PasswordHasher()
DUMMY_PASSWORD_HASH = password_hasher.hash(secrets.token_urlsafe(24))


def token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_authenticated_user(token):
    con = db()
    try:
        row = con.execute("""
            SELECT auth_users.id, auth_users.username, auth_users.display_name,
                   auth_sessions.expires_at
            FROM auth_sessions
            JOIN auth_users ON auth_users.id = auth_sessions.user_id
            WHERE auth_sessions.token_hash = ? AND auth_users.is_active = 1
        """, (token_digest(token),)).fetchone()

        if not row:
            return None
        if row["expires_at"] <= now_text():
            con.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (token_digest(token),),
            )
            con.commit()
            return None

        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
        }
    finally:
        con.close()


def auth_setup_required():
    con = db()
    try:
        count = con.execute(
            "SELECT COUNT(*) AS total FROM auth_users"
        ).fetchone()["total"]
        return count < 2
    finally:
        con.close()


@app.get("/api/auth/status")
def auth_status(request: Request):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    user = get_authenticated_user(token) if token else None
    return {
        "authenticated": user is not None,
        "setup_required": auth_setup_required(),
        "user": user,
    }


@app.post("/api/auth/setup")
def setup_authentication(req: AuthSetup):
    expected_token = os.getenv("AUTH_SETUP_TOKEN", "")
    if not expected_token:
        raise HTTPException(
            503,
            "Account setup is disabled until AUTH_SETUP_TOKEN is configured.",
        )
    if not hmac.compare_digest(req.setup_token, expected_token):
        raise HTTPException(403, "The setup token is invalid.")

    con = db()
    try:
        con.execute("BEGIN IMMEDIATE")
        count = con.execute(
            "SELECT COUNT(*) AS total FROM auth_users"
        ).fetchone()["total"]
        if count:
            raise HTTPException(409, "Initial account setup has already been completed.")

        con.execute("""
            INSERT INTO auth_users(username, display_name, password_hash)
            VALUES (?, ?, ?), (?, ?, ?)
        """, (
            "hunta", "Hunta", password_hasher.hash(req.hunta_password),
            "jay", "Jay", password_hasher.hash(req.jay_password),
        ))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    return {"ok": True, "message": "Hunta and Jay accounts are ready. Please sign in."}


@app.post("/api/auth/login")
def login(req: AuthLogin, response: Response):
    username = req.username.lower()
    con = db()
    try:
        row = con.execute("""
            SELECT id, username, display_name, password_hash,
                   failed_attempts, locked_until, is_active
            FROM auth_users
            WHERE username = ?
        """, (username,)).fetchone()

        failed_attempts = int(row["failed_attempts"]) if row else 0
        if row and row["locked_until"]:
            if row["locked_until"] > now_text():
                raise HTTPException(
                    423,
                    "This account is temporarily locked after failed sign-in attempts.",
                )
            failed_attempts = 0
            con.execute("""
                UPDATE auth_users
                SET failed_attempts = 0, locked_until = NULL
                WHERE id = ?
            """, (row["id"],))
            con.commit()

        stored_hash = row["password_hash"] if row else DUMMY_PASSWORD_HASH
        verified = False
        try:
            password_hasher.verify(stored_hash, req.password)
            verified = True
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            verified = False

        if not row or not row["is_active"] or not verified:
            if row:
                attempts = failed_attempts + 1
                locked_until = (
                    (now() + timedelta(minutes=15)).isoformat()
                    if attempts >= 5 else None
                )
                con.execute("""
                    UPDATE auth_users
                    SET failed_attempts = ?, locked_until = ?
                    WHERE id = ?
                """, (attempts, locked_until, row["id"]))
                con.commit()
            raise HTTPException(401, "Username or password is incorrect.")

        con.execute("""
            UPDATE auth_users
            SET failed_attempts = 0, locked_until = NULL, last_login = ?
            WHERE id = ?
        """, (now_text(), row["id"]))

        token = secrets.token_urlsafe(32)
        expires_at = (now() + timedelta(hours=AUTH_SESSION_HOURS)).isoformat()
        con.execute("""
            INSERT INTO auth_sessions(token_hash, user_id, expires_at)
            VALUES (?, ?, ?)
        """, (token_digest(token), row["id"], expires_at))
        con.commit()

        response.set_cookie(
            AUTH_COOKIE_NAME,
            token,
            max_age=AUTH_SESSION_HOURS * 60 * 60,
            httponly=True,
            secure=AUTH_COOKIE_SECURE,
            samesite="strict",
            path="/",
        )
        return {
            "ok": True,
            "user": {
                "username": row["username"],
                "display_name": row["display_name"],
            },
            "expires_at": expires_at,
        }
    finally:
        con.close()


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        con = db()
        try:
            con.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (token_digest(token),),
            )
            con.commit()
        finally:
            con.close()

    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path="/",
        secure=AUTH_COOKIE_SECURE,
        httponly=True,
        samesite="strict",
    )
    return {"ok": True}



# ============================================================
# ODDS AND ROI CALCULATIONS
# ============================================================

def net_decimal_odds(odds, commission_pct=0):
    commission = commission_pct / 100
    return 1 + (odds - 1) * (1 - commission)


def calculate(candidate: Candidate):
    adjusted = [
        net_decimal_odds(o.odds, o.commission_pct)
        for o in candidate.outcomes
    ]

    inverse_sum = sum(1 / odds for odds in adjusted)
    roi = (1 / inverse_sum) - 1

    stakes = [
        candidate.total_stake * (1 / odds) / inverse_sum
        for odds in adjusted
    ]

    stakes = [round(x, 2) for x in stakes]
    stakes[-1] = round(
        candidate.total_stake - sum(stakes[:-1]), 2
    )

    payout = min(
        stakes[i] * adjusted[i]
        for i in range(len(stakes))
    )

    return {
        "sum_inverse": inverse_sum,
        "roi": roi,
        "stakes": stakes,
        "equalised_payout": payout,
        "profit": payout - candidate.total_stake,
        "adjusted_odds": adjusted,
    }


def calculate_position_roi(legs, possible_outcomes, commission_by_account):
    total_stake = sum(leg.stake for leg in legs)

    if total_stake <= 0:
        raise ValueError("Total stake must be positive.")

    scenario_results = []

    for outcome_id in possible_outcomes:
        total_return = 0.0

        for leg in legs:
            if leg.outcome_id == outcome_id:
                total_return += (
                    leg.stake
                    * net_decimal_odds(
                        leg.odds,
                        commission_by_account[leg.account_id],
                    )
                )

        profit = total_return - total_stake
        roi = profit / total_stake

        scenario_results.append({
            "outcome_id": outcome_id,
            "total_return": round(total_return, 2),
            "profit": round(profit, 2),
            "roi": roi,
        })

    worst_case = min(
        scenario_results,
        key=lambda result: result["profit"],
    )

    return {
        "total_stake": round(total_stake, 2),
        "minimum_payout": worst_case["total_return"],
        "expected_profit": worst_case["profit"],
        "roi": worst_case["roi"],
        "scenarios": scenario_results,
    }


# ============================================================
# EXPOSURE AND BUFFER CONTROLS
# ============================================================

def get_account_balance(con, account_id):
    row = con.execute("""
        SELECT accounts.opening_balance + COALESCE(SUM(account_transactions.amount), 0) AS balance
        FROM accounts
        LEFT JOIN account_transactions ON account_transactions.account_id = accounts.id
        WHERE accounts.id = ?
        GROUP BY accounts.id
    """, (account_id,)).fetchone()
    return round(float(row["balance"]), 2) if row else None


def get_open_exposure(con):
    row = con.execute("""
        SELECT COALESCE(SUM(total_stake), 0) AS exposure
        FROM positions
        WHERE status IN ('Open', 'Unmatched')
    """).fetchone()

    return round(float(row["exposure"]), 2)


def get_buffer_state(con):
    rows = con.execute("""
        SELECT
            COALESCE(SUM(
                CASE WHEN status = 'committed'
                THEN amount ELSE 0 END
            ), 0) AS committed,
            COALESCE(SUM(
                CASE WHEN status = 'resolved'
                THEN amount ELSE 0 END
            ), 0) AS resolved
        FROM buffer_ledger
    """).fetchone()

    committed = round(float(rows["committed"]), 2)

    overdue = con.execute("""
        SELECT COUNT(*) AS total
        FROM buffer_ledger
        WHERE status = 'committed'
        AND due_at < ?
    """, (now_text(),)).fetchone()["total"]

    return {
        "limit": SETTLEMENT_BUFFER_LIMIT,
        "committed": committed,
        "available": round(
            SETTLEMENT_BUFFER_LIMIT - committed, 2
        ),
        "overdue_items": overdue,
        "new_positions_paused": overdue > 0,
    }


def check_new_position_allowed(con, additional_exposure):
    buffer = get_buffer_state(con)

    unmatched = con.execute("""
        SELECT COUNT(*) AS total
        FROM positions
        WHERE status = 'Unmatched'
    """).fetchone()["total"]
    if unmatched:
        raise HTTPException(
            409,
            "New positions paused: resolve the unmatched position before continuing.",
        )

    if buffer["overdue_items"] > 0:
        raise HTTPException(
            409,
            "New positions paused: a settlement-buffer commitment "
            "has exceeded its 24-hour deadline.",
        )

    current = get_open_exposure(con)
    proposed = round(current + additional_exposure, 2)

    if proposed > MAX_EXPOSURE:
        raise HTTPException(
            409,
            f"Exposure limit exceeded. Current: ${current:.2f}; "
            f"proposed: ${proposed:.2f}; limit: ${MAX_EXPOSURE:.2f}.",
        )


# ============================================================
# PROFIT ALLOCATION
# ============================================================

def allocate_realised_profit(con, position_id, profit):
    if profit <= 0:
        return {
            "reinvested": 0,
            "withdrawal": 0,
        }

    reinvested = round(
        profit * REINVESTMENT_PERCENT, 2
    )

    withdrawal = round(
        profit - reinvested, 2
    )

    con.execute("""
        INSERT INTO profit_allocations (
            position_id,
            realised_profit,
            reinvested_amount,
            withdrawal_amount
        )
        VALUES (?, ?, ?, ?)
    """, (
        position_id,
        profit,
        reinvested,
        withdrawal,
    ))

    con.execute("""
        INSERT INTO bankroll_ledger (
            owner, entry_type, amount, description, reference_id
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        None,
        "realised_profit",
        profit,
        "Realised position profit",
        position_id,
    ))

    return {
        "reinvested": reinvested,
        "withdrawal": withdrawal,
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():
    initialise_system()


@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "service": "australian-arbitrage-control",
        "mode": "manual-only",
        "version": "1.0.0",
    }


# ============================================================
# CONFIGURATION AND DASHBOARD DATA
# ============================================================

@app.get("/api/config")
def config():
    return {
        "starting_bankroll": STARTING_BANKROLL,
        "hunta_allocation": HUNTA_ALLOCATION,
        "jay_allocation": JAY_ALLOCATION,
        "settlement_buffer": SETTLEMENT_BUFFER_LIMIT,
        "max_exposure": MAX_EXPOSURE,
        "max_exposure_percent": MAX_EXPOSURE_PERCENT,
        "protected_reserve_percent": PROTECTED_RESERVE_PERCENT,
        "minimum_net_roi": MIN_NET_ROI,
        "reinvestment_percent": REINVESTMENT_PERCENT,
        "withdrawal_percent": WITHDRAWAL_PERCENT,
        "bookmakers": BOOKMAKERS,
        "bet_placement": "manual only",
        "concurrent_position_limit": "unlimited within exposure cap",
    }


@app.get("/api/accounts")
def accounts():
    with db() as con:
        rows = con.execute("""
            SELECT
                accounts.id, accounts.owner, accounts.bookmaker,
                accounts.opening_balance, accounts.status,
                ROUND(accounts.opening_balance + COALESCE(SUM(account_transactions.amount), 0), 2) AS balance
            FROM accounts
            LEFT JOIN account_transactions
                ON account_transactions.account_id = accounts.id
            GROUP BY accounts.id
            ORDER BY accounts.owner, accounts.bookmaker
        """).fetchall()

    return [dict(row) for row in rows]


@app.post("/api/accounts/{account_id}/opening-balance")
def set_opening_balance(account_id: int, req: OpeningBalance, request: Request):
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        account = con.execute(
            "SELECT id, opening_balance FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not account:
            raise HTTPException(404, "Bookmaker account not found.")
        transactions = con.execute(
            "SELECT COUNT(*) AS total FROM account_transactions WHERE account_id = ?",
            (account_id,),
        ).fetchone()["total"]
        legs = con.execute(
            "SELECT COUNT(*) AS total FROM position_legs WHERE account_id = ?",
            (account_id,),
        ).fetchone()["total"]
        if transactions or legs or float(account["opening_balance"]) != 0:
            raise HTTPException(
                409,
                "Opening balance can only be set before this account has ledger activity.",
            )
        con.execute(
            "UPDATE accounts SET opening_balance = ? WHERE id = ?",
            (req.amount, account_id),
        )
        con.execute(
            "INSERT INTO audit_log(action, details) VALUES (?, ?)",
            ("account_opening_balance_set", json.dumps({
                "account_id": account_id,
                "amount": req.amount,
                "confirmed_by": request.state.auth_user["username"],
            })),
        )

    return {"ok": True, "balance": round(req.amount, 2)}


@app.post("/api/accounts/{account_id}/transactions")
def record_account_transaction(account_id: int, req: AccountTransaction, request: Request):
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        account = con.execute(
            "SELECT id FROM accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        if not account:
            raise HTTPException(404, "Bookmaker account not found.")
        amount = req.amount if req.transaction_type == "deposit" else -req.amount
        current_balance = get_account_balance(con, account_id)
        if amount < 0 and current_balance < req.amount:
            raise HTTPException(409, "Withdrawal exceeds the recorded balance.")
        con.execute("""
            INSERT INTO account_transactions (
                account_id, transaction_type, amount, description
            )
            VALUES (?, ?, ?, ?)
        """, (account_id, req.transaction_type, amount, req.description.strip()))
        con.execute(
            "INSERT INTO audit_log(action, details) VALUES (?, ?)",
            ("account_transaction_recorded", json.dumps({
                "account_id": account_id,
                "transaction_type": req.transaction_type,
                "amount": amount,
                "confirmed_by": request.state.auth_user["username"],
            })),
        )
        balance = get_account_balance(con, account_id)

    return {"ok": True, "balance": balance}


@app.get("/api/dashboard")
def dashboard():
    with db() as con:
        exposure = get_open_exposure(con)
        buffer = get_buffer_state(con)

        open_count = con.execute("""
            SELECT COUNT(*) AS total
            FROM positions
            WHERE status IN ('Open', 'Unmatched')
        """).fetchone()["total"]

        settled = con.execute("""
            SELECT
                COUNT(*) AS total,
                COALESCE((
                    SELECT SUM(actual_pnl)
                    FROM positions
                    WHERE status = 'Settled'
                ), 0) AS profit
            FROM positions
            WHERE status = 'Settled'
        """).fetchone()

        allocations = con.execute("""
            SELECT
                COALESCE(SUM(reinvested_amount), 0) AS reinvested,
                COALESCE(SUM(withdrawal_amount), 0) AS withdrawals
            FROM profit_allocations
        """).fetchone()

    return {
        "starting_bankroll": STARTING_BANKROLL,
        "maximum_exposure": MAX_EXPOSURE,
        "open_exposure": exposure,
        "remaining_exposure": round(
            MAX_EXPOSURE - exposure, 2
        ),
        "open_positions": open_count,
        "settled_positions": settled["total"],
        "realised_profit_loss": round(
            float(settled["profit"]), 2
        ),
        "reinvested_profit": round(
            float(allocations["reinvested"]), 2
        ),
        "withdrawal_allocation": round(
            float(allocations["withdrawals"]), 2
        ),
        "buffer": buffer,
    }


# ============================================================
# OPPORTUNITY SCANNER - MANUAL ODDS INPUT FOR NOW
# ============================================================

@app.post("/api/scan")
def scan(candidate: Candidate):
    result = calculate(candidate)

    if result["roi"] < MIN_NET_ROI:
        return {
            "qualified": False,
            "reason": "Below 1.5% minimum calculated net ROI",
            **result,
            "candidate": candidate.model_dump(),
        }

    opportunity_id = str(uuid.uuid4())
    data = candidate.model_dump()

    with db() as con:
        con.execute("""
            INSERT INTO opportunities
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            opportunity_id,
            now_text(),
            candidate.sport,
            candidate.event,
            candidate.market,
            json.dumps(data["outcomes"]),
            result["sum_inverse"],
            result["roi"],
            json.dumps(result["stakes"]),
            "Candidate",
        ))

    return {
        "qualified": True,
        "opportunity_id": opportunity_id,
        **result,
        "candidate": data,
        "warning": (
            "Indicative calculation only. Verify exact market rules, "
            "current odds, liquidity and commission manually."
        ),
    }


@app.get("/api/odds/sports")
def odds_sports():
    try:
        data, usage = fetch_json(
            os.getenv("ODDS_API_KEY", ""),
            "sports",
            {"all": "false"},
            base_url=os.getenv("ODDS_API_BASE", "https://api.the-odds-api.com/v4"),
        )
    except OddsProviderError as error:
        raise HTTPException(error.status_code, str(error)) from None
    if not isinstance(data, list):
        raise HTTPException(502, "The odds provider returned an invalid sports list.")
    return {"sports": data, "quota": usage}


@app.get("/api/odds/scan/{sport_key}")
def scan_live_odds(sport_key: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sport_key):
        raise HTTPException(400, "Invalid odds-provider sport key.")
    try:
        events, usage = fetch_json(
            os.getenv("ODDS_API_KEY", ""),
            f"sports/{sport_key}/odds",
            {"regions": "au", "markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso"},
            base_url=os.getenv("ODDS_API_BASE", "https://api.the-odds-api.com/v4"),
        )
    except OddsProviderError as error:
        raise HTTPException(error.status_code, str(error)) from None
    if not isinstance(events, list):
        raise HTTPException(502, "The odds provider returned an invalid event list.")

    results = []
    for raw in build_h2h_candidates(events):
        candidate_data = {
            "sport": raw["sport"],
            "event": raw["event"],
            "market": raw["market"],
            "outcomes": raw["outcomes"],
            "total_stake": 500.0,
        }
        candidate = Candidate(**candidate_data)
        result = calculate(candidate)
        if result["roi"] < MIN_NET_ROI:
            continue

        opportunity_id = str(uuid.uuid4())
        with db() as con:
            con.execute("""
                INSERT INTO opportunities
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                opportunity_id,
                now_text(),
                candidate.sport,
                candidate.event,
                candidate.market,
                json.dumps(candidate.model_dump()["outcomes"]),
                result["sum_inverse"],
                result["roi"],
                json.dumps(result["stakes"]),
                "Live candidate",
            ))
        results.append({
            "qualified": True,
            "opportunity_id": opportunity_id,
            **result,
            "candidate": candidate.model_dump(),
            "provider_event_id": raw["event_id"],
            "commence_time": raw["commence_time"],
            "source_updated_at": raw["source_updated_at"],
            "warning": (
                "Live odds are a candidate only. Confirm the event, exact market rules, "
                "current bookmaker prices, liquidity, and acceptance manually."
            ),
        })

    results.sort(key=lambda item: item["roi"], reverse=True)
    return {
        "sport_key": sport_key,
        "events_received": len(events),
        "qualified_count": len(results),
        "candidates": results,
        "quota": usage,
        "scanned_at": now_text(),
    }


@app.get("/api/opportunities")
def opportunities():
    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM opportunities
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()

    return [dict(row) for row in rows]


# ============================================================
# POSITION CREATION
# ============================================================

@app.post("/api/positions/open")
def open_position(req: OpenPosition, request: Request):
    return _create_position(req, request, allow_unmatched=False)


@app.post("/api/positions/unmatched")
def record_unmatched_position(req: OpenPosition, request: Request):
    if sum(1 for leg in req.legs if leg.manually_accepted) != 1:
        raise HTTPException(
            400,
            "An unmatched position must contain exactly one accepted leg.",
        )
    return _create_position(req, request, allow_unmatched=True)


def _create_position(req: OpenPosition, request: Request, allow_unmatched: bool):
    if not req.manual_confirmation:
        raise HTTPException(400, "Manual confirmation is required.")

    accepted_count = sum(1 for leg in req.legs if leg.manually_accepted)
    if not allow_unmatched and accepted_count != 2:
        raise HTTPException(400, "Both bookmaker legs must be manually accepted.")
    if allow_unmatched and accepted_count != 1:
        raise HTTPException(400, "Exactly one bookmaker leg must be manually accepted.")

    if not req.market_rules_verified:
        raise HTTPException(400, "Market rules must be manually verified.")

    if len(set(req.possible_outcomes)) != 2:
        raise HTTPException(400, "Exactly two distinct outcomes are required.")

    if any(not outcome.strip() for outcome in req.possible_outcomes):
        raise HTTPException(400, "Outcome IDs cannot be blank.")

    leg_outcomes = [leg.outcome_id for leg in req.legs]

    if len(set(leg_outcomes)) != 2:
        raise HTTPException(400, "Each leg must cover a different outcome.")

    if set(leg_outcomes) != set(req.possible_outcomes):
        raise HTTPException(400, "Both possible outcomes must be covered.")

    for leg in req.legs:
        if leg.event_id != req.event_id:
            raise HTTPException(400, "Leg event IDs do not match.")
        if leg.market_id != req.market_id:
            raise HTTPException(400, "Leg market IDs do not match.")

    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        account_ids = [leg.account_id for leg in req.legs]

        if len(set(account_ids)) != 2:
            raise HTTPException(400, "Two different accounts are required.")

        placeholders = ",".join("?" for _ in account_ids)
        account_rows = con.execute(
            f"""
            SELECT id, owner, bookmaker, status
            FROM accounts
            WHERE id IN ({placeholders})
            """,
            account_ids,
        ).fetchall()

        if len(account_rows) != 2:
            raise HTTPException(400, "One or more accounts do not exist.")

        owners = {row["owner"] for row in account_rows}

        if owners != {"Hunta", "Jay"}:
            raise HTTPException(
                400, "Each position requires one Hunta account and one Jay account."
            )

        if any(row["status"] != "active" for row in account_rows):
            raise HTTPException(409, "Both bookmaker accounts must be active.")

        for leg in req.legs:
            if leg.manually_accepted:
                available = get_account_balance(con, leg.account_id)
                if available is None or available < leg.stake:
                    raise HTTPException(
                        409,
                        f"Insufficient recorded balance for account {leg.account_id}; "
                        "update its opening balance or deposits first.",
                    )

        commission_by_account = {
            row["id"]: 6.0 if row["bookmaker"] == "Betfair" else 0.0
            for row in account_rows
        }
        betfair_account_ids = [
            row["id"] for row in account_rows
            if row["bookmaker"] == "Betfair"
        ]

        for betfair_account_id in betfair_account_ids:
            existing_bet = con.execute("""
                SELECT id
                FROM position_legs
                WHERE account_id = ?
                  AND settled = 0
                LIMIT 1
            """, (betfair_account_id,)).fetchone()

            if existing_bet:
                owner = next(
                    row["owner"] for row in account_rows
                    if row["id"] == betfair_account_id
                )
                raise HTTPException(
                    409,
                    f"{owner}'s Betfair account already has an unsettled bet. "
                    "Settle that Betfair leg before opening another position."
                )
        result = calculate_position_roi(
            req.legs,
            req.possible_outcomes,
            commission_by_account,
        )

        if result["roi"] < MIN_NET_ROI:
            raise HTTPException(
                400,
                f"Worst-case ROI is below 1.5%. Calculated: {result['roi']:.2%}.",
            )

        check_new_position_allowed(con, result["total_stake"])

        position_id = con.execute("""
            INSERT INTO positions (
                event, sport, market, status, total_stake,
                expected_profit, expected_roi, manual_confirmed, notes,
                event_id, market_id, market_type,
                possible_outcomes_json, market_rules_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 1)
        """, (
            req.event, req.sport, req.market,
            "Unmatched" if allow_unmatched else "Open",
            result["total_stake"], result["expected_profit"],
            result["roi"], req.notes, req.event_id, req.market_id,
            req.market_type, json.dumps(req.possible_outcomes),
        )).lastrowid

        for leg in req.legs:
            leg_id = con.execute("""
                INSERT INTO position_legs (
                    position_id, account_id, selection,
                    odds, stake, accepted, settled,
                    event_id, market_id, outcome_id, commission_pct
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position_id, leg.account_id, leg.selection,
                leg.odds, leg.stake, int(leg.manually_accepted),
                0 if leg.manually_accepted else 1,
                leg.event_id, leg.market_id, leg.outcome_id,
                commission_by_account[leg.account_id],
            )).lastrowid

            if leg.manually_accepted:
                con.execute("""
                    INSERT INTO account_transactions (
                        account_id, transaction_type, amount,
                        description, reference_id
                    )
                    VALUES (?, 'stake_debit', ?, ?, ?)
                """, (
                    leg.account_id, -leg.stake,
                    f"Position {position_id} accepted stake",
                    leg_id,
                ))

        con.execute("""
            INSERT INTO audit_log(action, details)
            VALUES (?, ?)
        """, (
            "position_unmatched" if allow_unmatched else "position_opened",
            json.dumps({
                "position_id": position_id,
                "event": req.event,
                "event_id": req.event_id,
                "market_id": req.market_id,
                "possible_outcomes": req.possible_outcomes,
                "total_stake": result["total_stake"],
                "roi": result["roi"],
                "scenarios": result["scenarios"],
                "owners": sorted(owners),
                "confirmed_by": request.state.auth_user["username"],
                "accepted_legs": accepted_count,
            }),
        ))

        notification_type = "position_unmatched" if allow_unmatched else "position_opened"
        notification_title = "Unmatched leg recorded" if allow_unmatched else "Position recorded"
        notification_message = f"{req.event}: {accepted_count} bookmaker leg(s) accepted. Review the dashboard for status."
        create_user_notifications(
            con, notification_type, position_id,
            notification_title, notification_message,
        )

    status = "Unmatched" if allow_unmatched else "Open"
    return {
        "ok": True,
        "position_id": position_id,
        "status": status,
        "confirmed_by": request.state.auth_user["username"],
        **result,
        "message": (
            "One accepted leg recorded as unmatched. New positions are paused "
            "until this position is settled."
            if allow_unmatched else
            "Both legs recorded as manually accepted. "
            "No bookmaker bets were placed by this application."
        ),
    }




@app.post("/api/positions/{position_id}/hedge")
def record_position_hedge(position_id: int, req: RecordHedge, request: Request):
    if not req.manual_confirmation or not req.manually_accepted:
        raise HTTPException(
            400,
            "Confirm that the hedge was manually placed and accepted before recording it.",
        )

    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        position = con.execute(
            "SELECT * FROM positions WHERE id = ?",
            (position_id,),
        ).fetchone()
        if not position:
            raise HTTPException(404, "Position not found.")
        if position["status"] != "Unmatched":
            raise HTTPException(409, "Only an unmatched position can accept a hedge.")

        accepted = con.execute(
            """
            SELECT l.account_id, l.selection, l.odds, l.stake, l.outcome_id,
                   a.bookmaker
            FROM position_legs l
            JOIN accounts a ON a.id = l.account_id
            WHERE l.position_id = ? AND l.accepted = 1
            """,
            (position_id,),
        ).fetchall()
        if len(accepted) != 1:
            raise HTTPException(409, "This position already has a recorded hedge.")

        outcomes = json.loads(position["possible_outcomes_json"] or "[]")
        if len(outcomes) != 2 or req.outcome_id not in outcomes:
            raise HTTPException(400, "The hedge must cover one of the recorded outcomes.")

        account = con.execute(
            "SELECT id, bookmaker, status FROM accounts WHERE id = ?",
            (req.account_id,),
        ).fetchone()
        if not account:
            raise HTTPException(404, "Bookmaker account not found.")
        if account["status"] != "active":
            raise HTTPException(409, "The bookmaker account is not active.")

        current_balance = get_account_balance(con, req.account_id)
        if current_balance is None or current_balance < req.stake:
            raise HTTPException(409, "Insufficient recorded balance for the hedge account.")

        if account["bookmaker"] == "Betfair":
            existing_bet = con.execute(
                """
                SELECT id FROM position_legs
                WHERE account_id = ? AND settled = 0
                LIMIT 1
                """,
                (req.account_id,),
            ).fetchone()
            if existing_bet:
                raise HTTPException(
                    409,
                    "This Betfair account already has an unsettled bet.",
                )

        commission_by_account = {}
        for leg in accepted:
            commission_by_account[leg["account_id"]] = (
                6.0 if leg["bookmaker"] == "Betfair" else 0.0
            )
        commission_by_account[req.account_id] = (
            6.0 if account["bookmaker"] == "Betfair" else 0.0
        )

        def worst_case_profit(legs):
            total_stake = sum(float(leg["stake"]) for leg in legs)
            scenario_profits = []
            for outcome in outcomes:
                total_return = sum(
                    float(leg["stake"]) * net_decimal_odds(
                        float(leg["odds"]),
                        commission_by_account[leg["account_id"]],
                    )
                    for leg in legs
                    if leg["outcome_id"] == outcome
                )
                scenario_profits.append(total_return - total_stake)
            return min(scenario_profits), total_stake

        baseline_profit, _ = worst_case_profit(accepted)
        hedge = {
            "account_id": req.account_id,
            "selection": req.selection.strip(),
            "odds": req.odds,
            "stake": req.stake,
            "outcome_id": req.outcome_id,
        }
        improved_profit, accepted_stake_total = worst_case_profit([*accepted, hedge])
        if improved_profit <= baseline_profit + 0.005:
            raise HTTPException(
                400,
                "This hedge does not improve the calculated worst-case return. "
                "Review the outcome, odds, and stake.",
            )

        current_exposure = get_open_exposure(con)
        if current_exposure + req.stake > MAX_EXPOSURE:
            raise HTTPException(
                409,
                f"Exposure limit exceeded. Current: ${current_exposure:.2f}; "
                f"hedge: ${req.stake:.2f}; limit: ${MAX_EXPOSURE:.2f}.",
            )

        leg_id = con.execute(
            """
            INSERT INTO position_legs (
                position_id, account_id, selection, odds, stake, accepted, settled,
                event_id, market_id, outcome_id, commission_pct
            ) VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?)
            """,
            (
                position_id, req.account_id, req.selection.strip(), req.odds,
                req.stake, position["event_id"], position["market_id"],
                req.outcome_id, commission_by_account[req.account_id],
            ),
        ).lastrowid
        con.execute(
            """
            INSERT INTO account_transactions (
                account_id, transaction_type, amount, description, reference_id
            ) VALUES (?, 'stake_debit', ?, ?, ?)
            """,
            (
                req.account_id, -req.stake,
                f"Position {position_id} manually accepted hedge",
                leg_id,
            ),
        )
        con.execute(
            """
            UPDATE positions
            SET total_stake = total_stake + ?,
                expected_profit = ?,
                expected_roi = ?
            WHERE id = ?
            """,
            (
                req.stake, round(improved_profit, 2),
                improved_profit / accepted_stake_total, position_id,
            ),
        )
        con.execute(
            "INSERT INTO audit_log(action, details) VALUES (?, ?)",
            (
                "position_hedge_recorded",
                json.dumps({
                    "position_id": position_id,
                    "leg_id": leg_id,
                    "account_id": req.account_id,
                    "outcome_id": req.outcome_id,
                    "stake": req.stake,
                    "odds": req.odds,
                    "baseline_worst_case_profit": round(baseline_profit, 2),
                    "hedged_worst_case_profit": round(improved_profit, 2),
                    "recorded_by": request.state.auth_user["username"],
                    "notes": req.notes,
                }),
            ),
        )

    return {
        "ok": True,
        "position_id": position_id,
        "leg_id": leg_id,
        "position_status": "Unmatched",
        "worst_case_profit_before": round(baseline_profit, 2),
        "worst_case_profit_after": round(improved_profit, 2),
        "message": (
            "Manually accepted hedge recorded. New positions remain paused "
            "until every accepted leg in this position is settled."
        ),
    }

def create_user_notifications(con, event_type, position_id, title, message):
    users = con.execute(
        "SELECT id FROM auth_users WHERE is_active = 1"
    ).fetchall()
    con.executemany(
        """
        INSERT INTO user_notifications (
            user_id, event_type, position_id, title, message
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (user["id"], event_type, position_id, title, message)
            for user in users
        ],
    )


@app.get("/api/notifications")
def list_notifications(request: Request):
    user_id = request.state.auth_user["id"]
    with db() as con:
        rows = con.execute(
            """
            SELECT id, event_type, position_id, title, message, created_at, read_at
            FROM user_notifications
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 50
            """,
            (user_id,),
        ).fetchall()
        unread = con.execute(
            "SELECT COUNT(*) AS total FROM user_notifications WHERE user_id = ? AND read_at IS NULL",
            (user_id,),
        ).fetchone()["total"]
    return {"items": [dict(row) for row in rows], "unread_count": unread}


@app.post("/api/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, request: Request):
    user_id = request.state.auth_user["id"]
    with db() as con:
        cursor = con.execute(
            """
            UPDATE user_notifications
            SET read_at = ?
            WHERE id = ? AND user_id = ? AND read_at IS NULL
            """,
            (now_text(), notification_id, user_id),
        )
        if cursor.rowcount == 0:
            exists = con.execute(
                "SELECT 1 FROM user_notifications WHERE id = ? AND user_id = ?",
                (notification_id, user_id),
            ).fetchone()
            if not exists:
                raise HTTPException(404, "Notification not found.")
    return {"ok": True}



# ============================================================
# POSITION TRACKING AND SETTLEMENT
# ============================================================

@app.get("/api/reports/summary")
def reports_summary():
    with db() as con:
        totals = con.execute("""
            SELECT
                COUNT(*) AS all_positions,
                SUM(CASE WHEN status = 'Open' THEN 1 ELSE 0 END) AS open_positions,
                SUM(CASE WHEN status = 'Unmatched' THEN 1 ELSE 0 END) AS unmatched_positions,
                SUM(CASE WHEN status = 'Settled' THEN 1 ELSE 0 END) AS settled_positions,
                COALESCE(SUM(CASE WHEN status = 'Settled' THEN actual_pnl ELSE 0 END), 0) AS net_realised_pnl,
                COALESCE(SUM(CASE WHEN status IN ('Open', 'Unmatched') THEN total_stake ELSE 0 END), 0) AS committed_exposure
            FROM positions
        """).fetchone()
        commissions = con.execute("""
            SELECT COALESCE(SUM(commission_paid), 0) AS total
            FROM position_legs
        """).fetchone()["total"]
        allocations = con.execute("""
            SELECT
                COALESCE(SUM(reinvested_amount), 0) AS reinvested,
                COALESCE(SUM(withdrawal_amount), 0) AS withdrawn
            FROM profit_allocations
        """).fetchone()
        months = con.execute("""
            SELECT substr(settled_at, 1, 7) AS month,
                   COUNT(*) AS positions,
                   ROUND(COALESCE(SUM(actual_pnl), 0), 2) AS net_pnl
            FROM positions
            WHERE status = 'Settled'
            GROUP BY substr(settled_at, 1, 7)
            ORDER BY month DESC
        """).fetchall()

    return {
        "positions": dict(totals),
        "commission_paid": round(float(commissions), 2),
        "reinvested": round(float(allocations["reinvested"]), 2),
        "withdrawn": round(float(allocations["withdrawn"]), 2),
        "monthly": [dict(row) for row in months],
    }


@app.get("/api/reports/settlements.csv")
def settlements_csv():
    with db() as con:
        rows = con.execute("""
            SELECT p.id, p.settled_at, p.sport, p.event, p.market,
                   p.total_stake, p.expected_roi, p.actual_pnl,
                   COALESCE(SUM(l.commission_paid), 0) AS commission_paid
            FROM positions p
            LEFT JOIN position_legs l ON l.position_id = p.id
            WHERE p.status = 'Settled'
            GROUP BY p.id
            ORDER BY p.settled_at DESC, p.id DESC
        """).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "position_id", "settled_at", "sport", "event", "market",
        "planned_stake", "expected_roi", "actual_pnl", "commission_paid",
    ])
    for row in rows:
        writer.writerow([row[key] for key in row.keys()])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="arbitrage-settlements.csv"'},
    )


@app.get("/api/positions")
def positions():
    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM positions
            ORDER BY id DESC
            LIMIT 200
        """).fetchall()

        result = []

        for row in rows:
            item = dict(row)

            legs = con.execute("""
                SELECT
                    position_legs.*,
                    accounts.owner,
                    accounts.bookmaker
                FROM position_legs
                JOIN accounts
                    ON accounts.id = position_legs.account_id
                WHERE position_legs.position_id = ?
                ORDER BY position_legs.id
            """, (row["id"],)).fetchall()

            item["legs"] = [dict(leg) for leg in legs]
            result.append(item)

    return result


@app.post("/api/positions/{position_id}/legs/{leg_id}/settle")
def settle_leg(position_id: int, leg_id: int, req: SettleLeg):
    with db() as con:
        position = con.execute("""
            SELECT *
            FROM positions
            WHERE id = ?
        """, (position_id,)).fetchone()

        if not position:
            raise HTTPException(404, "Position not found.")

        if position["status"] not in ("Open", "Unmatched"):
            raise HTTPException(409, "Position is not open or unmatched.")

        leg = con.execute("""
            SELECT *
            FROM position_legs
            WHERE id = ? AND position_id = ?
        """, (leg_id, position_id)).fetchone()

        if not leg:
            raise HTTPException(404, "Position leg not found.")

        if leg["settled"]:
            raise HTTPException(409, "This leg is already settled.")
        if not leg["accepted"]:
            raise HTTPException(409, "A leg that was not accepted cannot be settled.")

        commission_pct = float(leg["commission_pct"] or 0)
        gross_profit = max(req.actual_return - leg["stake"], 0)
        commission_paid = round(
            gross_profit * commission_pct / 100,
            2,
        )

        con.execute("""
            UPDATE position_legs
            SET settled = 1, actual_return = ?, commission_paid = ?
            WHERE id = ?
        """, (req.actual_return, commission_paid, leg_id))

        con.execute("""
            INSERT INTO account_transactions (
                account_id, transaction_type, amount,
                description, reference_id
            )
            VALUES (?, 'settlement_return', ?, ?, ?)
        """, (
            leg["account_id"],
            round(req.actual_return - commission_paid, 2),
            f"Position {position_id} settlement return after commission",
            leg_id,
        ))

        remaining = con.execute("""
            SELECT COUNT(*) AS total
            FROM position_legs
            WHERE position_id = ? AND settled = 0
        """, (position_id,)).fetchone()["total"]

        if remaining > 0:
            return {
                "ok": True,
                "leg_settled": True,
                "position_status": "Open",
                "remaining_legs": remaining,
                "commission_paid": commission_paid,
                "message": (
                    "One leg settled. Full position exposure remains "
                    "committed until both legs settle."
                ),
            }

        totals = con.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN accepted = 1 THEN stake ELSE 0 END), 0) AS total_stake,
                COALESCE(SUM(CASE WHEN accepted = 1 THEN actual_return ELSE 0 END), 0) AS total_return,
                COALESCE(SUM(commission_paid), 0) AS total_commission
            FROM position_legs
            WHERE position_id = ?
        """, (position_id,)).fetchone()

        pnl = round(
            float(totals["total_return"]) -
            float(totals["total_stake"]) -
            float(totals["total_commission"]),
            2,
        )

        con.execute("""
            UPDATE positions
            SET status = 'Settled', settled_at = ?,
                actual_pnl = ?
            WHERE id = ?
        """, (now_text(), pnl, position_id))

        allocation = allocate_realised_profit(
            con, position_id, pnl
        )

        con.execute("""
            INSERT INTO audit_log(action, details)
            VALUES (?, ?)
        """, (
            "position_settled",
            json.dumps({
                "position_id": position_id,
                "actual_pnl": pnl,
                "total_commission": totals["total_commission"],
                "allocation": allocation,
            }),
        ))

        create_user_notifications(
            con,
            "position_settled",
            position_id,
            "Position settled",
            f"{position['event']}: net P/L {pnl:.2f}.",
        )

    return {
        "ok": True,
        "position_status": "Settled",
        "actual_pnl": pnl,
        "total_commission": totals["total_commission"],
        **allocation,
        "message": (
            "Unmatched exposure closed after the accepted leg was settled."
            if position["status"] == "Unmatched"
            else "Both legs settled. Position exposure released."
        ),
    }


# ============================================================
# SETTLEMENT BUFFER
# ============================================================

@app.get("/api/buffer")
def buffer_status():
    with db() as con:
        state = get_buffer_state(con)

        rows = con.execute("""
            SELECT *
            FROM buffer_ledger
            ORDER BY id DESC
            LIMIT 100
        """).fetchall()

    return {
        **state,
        "entries": [dict(row) for row in rows],
    }


@app.post("/api/buffer/commit")
def commit_buffer(req: BufferCommit):
    with db() as con:
        state = get_buffer_state(con)

        if state["overdue_items"] > 0:
            raise HTTPException(
                409,
                "Buffer already has an overdue commitment.",
            )

        if req.amount > state["available"]:
            raise HTTPException(
                409,
                "Settlement buffer limit would be exceeded.",
            )

        if req.position_id is not None:
            position = con.execute("""
                SELECT id FROM positions WHERE id = ?
            """, (req.position_id,)).fetchone()

            if not position:
                raise HTTPException(404, "Position not found.")

        due_at = (
            now() + timedelta(hours=BUFFER_DEADLINE_HOURS)
        ).isoformat()

        entry_id = con.execute("""
            INSERT INTO buffer_ledger (
                position_id, amount, status, due_at, description
            )
            VALUES (?, ?, 'committed', ?, ?)
        """, (
            req.position_id,
            req.amount,
            due_at,
            req.description,
        )).lastrowid

        con.execute("""
            INSERT INTO audit_log(action, details)
            VALUES (?, ?)
        """, (
            "buffer_committed",
            json.dumps({
                "entry_id": entry_id,
                "amount": req.amount,
                "due_at": due_at,
            }),
        ))

    return {
        "ok": True,
        "buffer_entry_id": entry_id,
        "amount": req.amount,
        "due_at": due_at,
        "message": "Buffer commitment recorded.",
    }


@app.post("/api/buffer/{entry_id}/resolve")
def resolve_buffer(entry_id: int, req: BufferResolve):
    with db() as con:
        row = con.execute("""
            SELECT *
            FROM buffer_ledger
            WHERE id = ?
        """, (entry_id,)).fetchone()

        if not row:
            raise HTTPException(404, "Buffer entry not found.")

        if row["status"] != "committed":
            raise HTTPException(
                409,
                "Buffer entry is not awaiting resolution.",
            )

        con.execute("""
            UPDATE buffer_ledger
            SET status = 'resolved', resolved_at = ?,
                description = ?
            WHERE id = ?
        """, (
            now_text(),
            req.notes or row["description"],
            entry_id,
        ))

        con.execute("""
            INSERT INTO audit_log(action, details)
            VALUES (?, ?)
        """, (
            "buffer_resolved",
            json.dumps({"entry_id": entry_id}),
        ))

    return {
        "ok": True,
        "message": (
            "Buffer commitment marked resolved. "
            "Verify the actual funds have been replenished."
        ),
    }


# ============================================================
# LEGACY SET ROUTES - RETAINED FOR COMPATIBILITY
# ============================================================

@app.get("/api/sets")
def sets():
    with db() as con:
        rows = con.execute("""
            SELECT *
            FROM sets
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()

    return {
        "items": [dict(row) for row in rows],
        "notice": "Use /api/positions for the upgraded position system.",
    }


# ============================================================
# EMAIL AND PUSH NOTIFICATIONS
# ============================================================

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


def send_push(payload, user_id=None):
    try:
        from pywebpush import webpush

        pub = os.getenv("VAPID_PUBLIC_KEY")
        priv = os.getenv("VAPID_PRIVATE_KEY")
        subject = os.getenv(
            "VAPID_SUBJECT",
            "mailto:admin@example.com",
        )

        if not pub or not priv:
            return 0

        with db() as con:
            if user_id is None:
                rows = con.execute(
                    "SELECT subscription_json FROM push_subscriptions WHERE user_id IS NOT NULL"
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT subscription_json FROM push_subscriptions WHERE user_id = ?",
                    (user_id,),
                ).fetchall()

        sent = 0

        for row in rows:
            try:
                webpush(
                    json.loads(row["subscription_json"]),
                    json.dumps(payload),
                    vapid_private_key=priv,
                    vapid_claims={"sub": subject},
                )
                sent += 1
            except Exception:
                pass

        return sent

    except Exception:
        return 0


@app.post("/api/push/subscribe")
def subscribe(req: PushSub, request: Request):
    endpoint = req.subscription.get("endpoint")
    keys = req.subscription.get("keys")
    try:
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
    except ValueError:
        parsed = None
    if (
        parsed is None
        or parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or not isinstance(keys, dict)
        or not keys.get("p256dh")
        or not keys.get("auth")
    ):
        raise HTTPException(400, "A valid HTTPS push subscription is required.")

    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise HTTPException(400, "The push subscription host is not allowed.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise HTTPException(400, "The push subscription host is not allowed.")

    subscription_json = json.dumps(req.subscription, separators=(",", ":"), sort_keys=True)
    user_id = request.state.auth_user["id"]
    with db() as con:
        con.execute("""
            INSERT INTO push_subscriptions (user_id, subscription_json)
            VALUES (?, ?)
            ON CONFLICT(subscription_json)
            DO UPDATE SET user_id = excluded.user_id
        """, (user_id, subscription_json))

    return {"ok": True}


@app.get("/api/push/public-key")
def push_key():
    return {
        "public_key": os.getenv("VAPID_PUBLIC_KEY", "")
    }


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(
        "static/manifest.webmanifest",
        media_type="application/manifest+json",
    )


if __name__ == "__main__":
    initialise_system()
    print("Arbitrage control database initialised.")
