"""Customer API: independently authenticated, tenant-scoped read-only endpoints.

No legacy operator session grants customer access. Every query uses the tenant
resolved from the authenticated customer session, never a client-supplied ID.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError, InvalidHashError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.database import get_db

router = APIRouter(prefix="/api/customer", tags=["customer"])
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(24))
_COOKIE = "arb_customer_session"
_HOURS = 12


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _customer(request: Request):
    token = request.cookies.get(_COOKIE)
    if not token:
        raise HTTPException(401, "Customer sign-in required.")
    with get_db() as con:
        row = con.execute("""
            SELECT u.id AS user_id, u.tenant_id, u.role, u.email
            FROM customer_sessions s
            JOIN customer_users u
              ON u.id=s.user_id AND u.tenant_id=s.tenant_id
            JOIN customer_tenants t ON t.id=u.tenant_id
            WHERE s.token_hash=? AND s.expires_at>?
              AND u.active=1 AND t.status='active'
        """, (_digest(token), _utc())).fetchone()
    if row is None:
        raise HTTPException(401, "Customer session expired or inactive.")
    return dict(row)


class CustomerLogin(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def customer_login(payload: CustomerLogin, response: Response):
    # The tenant identifier is used only for login lookup, never as authority.
    with get_db() as con:
        row = con.execute("""
            SELECT u.id, u.tenant_id, u.email, u.role, u.password_hash,
                   u.failed_attempts, u.locked_until
            FROM customer_users u
            JOIN customer_tenants t ON t.id=u.tenant_id
            WHERE u.tenant_id=? AND u.email=? COLLATE NOCASE
              AND u.active=1 AND t.status='active'
        """, (payload.tenant_id, payload.email.strip())).fetchone()
        stored = row["password_hash"] if row else _DUMMY_HASH
        try:
            valid = _hasher.verify(stored, payload.password)
        except (VerificationError, VerifyMismatchError, InvalidHashError):
            valid = False

        # Serialize login decisions to avoid concurrent failures losing counts,
        # and verify the account wasn't disabled or re-keyed during hashing.
        con.execute("BEGIN IMMEDIATE")
        fresh = con.execute("""
            SELECT u.id, u.tenant_id, u.password_hash, u.failed_attempts,
                   u.locked_until, u.active, t.status AS tenant_status
            FROM customer_users u
            JOIN customer_tenants t ON t.id=u.tenant_id
            WHERE u.tenant_id=? AND u.email=? COLLATE NOCASE
        """, (payload.tenant_id, payload.email.strip())).fetchone()
        current_time = _utc()
        if fresh and fresh["locked_until"] and fresh["locked_until"] > current_time:
            raise HTTPException(423, "Customer account temporarily locked.")
        valid = bool(valid and row and fresh and
                     fresh["password_hash"] == stored and fresh["active"] == 1
                     and fresh["tenant_status"] == "active")
        if not valid:
            if fresh:
                attempts = (0 if fresh["locked_until"] else int(fresh["failed_attempts"])) + 1
                until = ((datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
                         if attempts >= 5 else None)
                con.execute("""
                    UPDATE customer_users SET failed_attempts=?, locked_until=?
                    WHERE id=? AND tenant_id=?
                """, (attempts, until, fresh["id"], fresh["tenant_id"]))
                con.commit()
            raise HTTPException(401, "Invalid customer credentials.")
        con.execute("""
            UPDATE customer_users SET failed_attempts=0, locked_until=NULL
            WHERE id=? AND tenant_id=?
        """, (fresh["id"], fresh["tenant_id"]))
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=_HOURS)).isoformat()
        con.execute("""
            INSERT INTO customer_sessions(token_hash, tenant_id, user_id, expires_at)
            VALUES (?,?,?,?)
        """, (_digest(token), fresh["tenant_id"], fresh["id"], expires))
        con.commit()
    response.set_cookie(_COOKIE, token, max_age=_HOURS * 3600,
                        httponly=True, secure=os.getenv("AUTH_COOKIE_SECURE", "true").lower()=="true",
                        samesite="strict", path="/api/customer")
    return {"authenticated": True, "expires_at": expires}


@router.post("/logout")
def customer_logout(request: Request, response: Response):
    token = request.cookies.get(_COOKIE)
    if token:
        with get_db() as con:
            con.execute("DELETE FROM customer_sessions WHERE token_hash=?", (_digest(token),))
            con.commit()
    response.delete_cookie(_COOKIE, path="/api/customer",
                           secure=os.getenv("AUTH_COOKIE_SECURE", "true").lower()=="true",
                           httponly=True, samesite="strict")
    return {"ok": True}


@router.get("/me")
def customer_me(request: Request):
    return _customer(request)


@router.get("/accounts")
def customer_accounts(request: Request):
    identity = _customer(request)
    with get_db() as con:
        rows = con.execute("""
            SELECT id, bookmaker, label, status, opening_balance_cents
            FROM customer_bookmaker_accounts WHERE tenant_id=?
            ORDER BY bookmaker, label
        """, (identity["tenant_id"],)).fetchall()
    return [dict(row) for row in rows]


@router.get("/positions")
def customer_positions(request: Request):
    identity = _customer(request)
    with get_db() as con:
        rows = con.execute("""
            SELECT id, event_id, market_id, status, total_stake_cents, created_at, settled_at
            FROM customer_positions WHERE tenant_id=?
            ORDER BY created_at DESC, id DESC LIMIT 200
        """, (identity["tenant_id"],)).fetchall()
    return [dict(row) for row in rows]


@router.get("/positions/{position_id}")
def customer_position(request: Request, position_id: str):
    identity = _customer(request)
    with get_db() as con:
        row = con.execute("""
            SELECT id, event_id, market_id, status, total_stake_cents, created_at, settled_at
            FROM customer_positions WHERE tenant_id=? AND id=?
        """, (identity["tenant_id"], position_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Position not found.")
        legs = con.execute("""
            SELECT id, account_id, outcome_id, odds, stake_cents, accepted, settled
            FROM customer_position_legs WHERE tenant_id=? AND position_id=?
            ORDER BY id
        """, (identity["tenant_id"], position_id)).fetchall()
    return {"position": dict(row), "legs": [dict(leg) for leg in legs]}


@router.get("/accounts/{account_id}")
def customer_account(request: Request, account_id: str):
    identity = _customer(request)
    with get_db() as con:
        row = con.execute("""
            SELECT id, bookmaker, label, status, opening_balance_cents
            FROM customer_bookmaker_accounts WHERE tenant_id=? AND id=?
        """, (identity["tenant_id"], account_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Account not found.")
        ledger = con.execute("""
            SELECT COALESCE(SUM(amount_cents),0) AS net_entries_cents
            FROM customer_bankroll_entries
            WHERE tenant_id=? AND account_id=?
        """, (identity["tenant_id"], account_id)).fetchone()
    result = dict(row)
    result["ledger_net_cents"] = int(ledger["net_entries_cents"])
    result["balance_cents"] = result["opening_balance_cents"] + result["ledger_net_cents"]
    return result


@router.get("/bankroll")
def customer_bankroll(request: Request):
    identity = _customer(request)
    with get_db() as con:
        rows = con.execute("""
            SELECT id, account_id, amount_cents, entry_type, created_at
            FROM customer_bankroll_entries WHERE tenant_id=?
            ORDER BY created_at DESC, id DESC LIMIT 200
        """, (identity["tenant_id"],)).fetchall()
    return [dict(row) for row in rows]


class CustomerAccountCreate(BaseModel):
    bookmaker: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=100)
    opening_balance_cents: int = Field(default=0, ge=0, le=1_000_000_000)


@router.post("/accounts", status_code=201)
def customer_create_account(payload: CustomerAccountCreate, request: Request):
    identity = _customer(request)
    if identity["role"] != "owner":
        raise HTTPException(403, "Customer owner permission required.")
    if not payload.bookmaker.strip() or not payload.label.strip():
        raise HTTPException(422, "Bookmaker and label cannot be blank.")
    account_id = str(uuid.uuid4())
    with get_db() as con:
        try:
            con.execute("""
                INSERT INTO customer_bookmaker_accounts
                    (id, tenant_id, bookmaker, label, opening_balance_cents)
                VALUES (?,?,?,?,?)
            """, (account_id, identity["tenant_id"],
                  payload.bookmaker.strip(), payload.label.strip(),
                  payload.opening_balance_cents))
            con.execute("""
                INSERT INTO customer_audit_log
                    (id, tenant_id, actor_user_id, action, details_json)
                VALUES (?,?,?,?,?)
            """, (str(uuid.uuid4()), identity["tenant_id"], identity["user_id"],
                  "customer_account_created",
                  json.dumps({"account_id": account_id})))
            con.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Account label already exists or is invalid.")
    return {"id": account_id, "bookmaker": payload.bookmaker.strip(),
            "label": payload.label.strip(),
            "opening_balance_cents": payload.opening_balance_cents}


@router.get("/audit")
def customer_audit(request: Request):
    identity = _customer(request)
    if identity["role"] != "owner":
        raise HTTPException(403, "Customer owner permission required.")
    with get_db() as con:
        rows = con.execute("""
            SELECT id, actor_user_id, action, details_json, created_at
            FROM customer_audit_log WHERE tenant_id=?
            ORDER BY created_at DESC, id DESC LIMIT 200
        """, (identity["tenant_id"],)).fetchall()
    return [dict(row) for row in rows]


class CustomerBankrollEntryCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=128)
    entry_type: str = Field(pattern="^(deposit|withdrawal|adjustment)$")
    amount_cents: int = Field(ge=-1_000_000_000, le=1_000_000_000)
    reference_id: str | None = Field(default=None, max_length=128)


@router.post("/bankroll", status_code=201)
def customer_record_bankroll(payload: CustomerBankrollEntryCreate, request: Request):
    identity = _customer(request)
    if identity["role"] != "owner":
        raise HTTPException(403, "Customer owner permission required.")
    if payload.amount_cents == 0:
        raise HTTPException(422, "Ledger amount must be nonzero.")
    if payload.entry_type == "deposit" and payload.amount_cents < 0:
        raise HTTPException(422, "Deposits must be positive.")
    if payload.entry_type == "withdrawal" and payload.amount_cents > 0:
        raise HTTPException(422, "Withdrawals must be negative.")
    entry_id = str(uuid.uuid4())
    with get_db() as con:
        con.execute("BEGIN IMMEDIATE")
        account = con.execute("""
            SELECT id FROM customer_bookmaker_accounts
            WHERE tenant_id=? AND id=? AND status='active'
        """, (identity["tenant_id"], payload.account_id)).fetchone()
        if account is None:
            raise HTTPException(404, "Active account not found.")
        try:
            con.execute("""
                INSERT INTO customer_bankroll_entries
                    (id, tenant_id, account_id, amount_cents, entry_type, reference_id)
                VALUES (?,?,?,?,?,?)
            """, (entry_id, identity["tenant_id"], payload.account_id,
                  payload.amount_cents, payload.entry_type, payload.reference_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Transaction reference already recorded.")
        con.execute("""
            INSERT INTO customer_audit_log
                (id, tenant_id, actor_user_id, action, details_json)
            VALUES (?,?,?,?,?)
        """, (str(uuid.uuid4()), identity["tenant_id"], identity["user_id"],
              "customer_bankroll_entry_created",
              json.dumps({"entry_id": entry_id, "account_id": payload.account_id,
                          "amount_cents": payload.amount_cents})))
        con.commit()
    return {"id": entry_id, "account_id": payload.account_id,
            "amount_cents": payload.amount_cents, "entry_type": payload.entry_type}
