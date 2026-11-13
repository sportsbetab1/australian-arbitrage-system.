"""Tenant-isolated customer API. Legacy routes are internal-only.

Public signup is intentionally disabled pending email verification and billing.
Provision users through a controlled administrative workflow.
"""
import os
import secrets
import sqlite3
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from app.database import get_db
from app.commercial_security import (
    AccessDenied, authenticate_limited, create_session, resolve_session, revoke_session,
    require_member, tenant_rows,
)
from app.commercial_schema import migrate_commercial_schema, migrate_legacy_tenant_columns
from app.commercial_billing import subscription_summary, fee_summary
from app.commercial_workspace import create_account, create_draft, record_bankroll_entry, workspace_summary, add_draft_leg, draft_detail, validate_draft, cancel_draft, restrict_account
from app.commercial_csrf import csrf_token, check_csrf

router = APIRouter(prefix="/api/customer", tags=["customer"])
COOKIE = "arb_customer_session"

class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

def conn():
    db = get_db()
    db.execute("PRAGMA foreign_keys=ON")
    return db

def current_user(request):
    token = request.cookies.get(COOKIE)
    with conn() as db:
        try:
            return resolve_session(db,token)
        except AccessDenied:
            raise HTTPException(401,"Authentication required")

def tenant_data(request, tenant_id, table):
    uid = current_user(request)
    with conn() as db:
        try:
            return [dict(row) for row in tenant_rows(db,uid,tenant_id,table)]
        except AccessDenied:
            raise HTTPException(404,"Workspace not found")

@router.post("/login")
def customer_login(body: Login, request: Request, response: Response):
    # Same-origin check is applied by the main application's API middleware.
    with conn() as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            uid = authenticate_limited(db,body.email,body.password)
        except (AccessDenied,ValueError):
            db.commit()  # Persist failed-login counter and lockout.
            raise HTTPException(401,"Invalid credentials")
        token = create_session(db,uid)
        db.commit()
    response.set_cookie(COOKIE,token,max_age=43200,httponly=True,
                        secure=os.getenv("AUTH_COOKIE_SECURE","true").lower()=="true",
                        samesite="strict",path="/api/customer")
    return {"authenticated":True}

@router.get("/csrf")
def customer_csrf(request: Request):
    current_user(request)
    return {"csrf_token":csrf_token(request.cookies.get(COOKIE))}

@router.post("/logout")
def customer_logout(request: Request, response: Response):
    check_csrf(request,COOKIE)
    token = request.cookies.get(COOKIE)
    if token:
        with conn() as db:
            revoke_session(db,token)
            db.commit()
    response.delete_cookie(COOKIE,path="/api/customer")
    return {"authenticated":False}

@router.get("/me")
def customer_me(request: Request):
    uid = current_user(request)
    with conn() as db:
        rows = db.execute("""SELECT m.tenant_id,m.role,t.display_name
            FROM commercial_memberships m JOIN commercial_tenants t ON t.id=m.tenant_id
            WHERE m.user_id=? AND m.status='active' AND t.status='active'""",(uid,)).fetchall()
    return {"workspaces":[dict(row) for row in rows]}

@router.get("/workspaces/{tenant_id}/accounts")
def customer_accounts(tenant_id: str, request: Request):
    return tenant_data(request,tenant_id,"commercial_customer_accounts")

@router.get("/workspaces/{tenant_id}/positions")
def customer_positions(tenant_id: str, request: Request):
    return tenant_data(request,tenant_id,"commercial_customer_positions")

@router.get("/workspaces/{tenant_id}/bankroll")
def customer_bankroll(tenant_id: str, request: Request):
    return tenant_data(request,tenant_id,"commercial_customer_bankroll_ledger")

@router.get("/workspaces/{tenant_id}/subscription")
def customer_subscription(tenant_id: str, request: Request):
    uid = current_user(request)
    with conn() as db:
        try:
            return subscription_summary(db,uid,tenant_id)
        except AccessDenied:
            raise HTTPException(404,"Workspace not found")

@router.get("/workspaces/{tenant_id}/fees")
def customer_fees(tenant_id: str, request: Request):
    uid = current_user(request)
    with conn() as db:
        try:
            return fee_summary(db,uid,tenant_id)
        except AccessDenied:
            raise HTTPException(404,"Workspace not found")

class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1,max_length=100)
    bookmaker: str = Field(min_length=1,max_length=100)
    opening_balance_cents: int = Field(default=0,ge=0,le=100000000000)

class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: str = Field(min_length=1,max_length=300)
    sport: str = Field(min_length=1,max_length=100)
    market: str = Field(min_length=1,max_length=200)
    total_stake_cents: int = Field(ge=0,le=100000000000)

class LedgerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_type: str
    amount_cents: int = Field(ge=-100000000000,le=100000000000)
    description: str = Field(min_length=4,max_length=500)

def mutate(request,tenant_id,fn,*args):
    check_csrf(request,COOKIE)
    uid=current_user(request)
    with conn() as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            result=fn(db,uid,tenant_id,*args)
            db.commit()
            return {"id":result}
        except AccessDenied:
            raise HTTPException(404,"Workspace not found")
        except ValueError as exc:
            raise HTTPException(422,str(exc))
        except sqlite3.IntegrityError:
            raise HTTPException(409,"Conflicting record")

@router.post("/workspaces/{tenant_id}/accounts",status_code=201)
def customer_create_account(tenant_id: str, body: AccountCreate, request: Request):
    return mutate(request,tenant_id,create_account,body.label,body.bookmaker,body.opening_balance_cents)

@router.post("/workspaces/{tenant_id}/positions/drafts",status_code=201)
def customer_create_draft(tenant_id: str, body: DraftCreate, request: Request):
    return mutate(request,tenant_id,create_draft,body.event,body.sport,body.market,body.total_stake_cents)

@router.post("/workspaces/{tenant_id}/bankroll",status_code=201)
def customer_record_bankroll(tenant_id: str, body: LedgerCreate, request: Request):
    return mutate(request,tenant_id,record_bankroll_entry,body.entry_type,body.amount_cents,body.description)

@router.get("/workspaces/{tenant_id}/summary")
def customer_workspace_summary(tenant_id: str, request: Request):
    uid=current_user(request)
    with conn() as db:
        try:
            return workspace_summary(db,uid,tenant_id)
        except AccessDenied:
            raise HTTPException(404,"Workspace not found")

@router.get("/workspaces/{tenant_id}/audit")
def customer_audit(tenant_id: str, request: Request):
    uid=current_user(request)
    with conn() as db:
        try:
            require_member(db,uid,tenant_id,("tenant_admin","viewer","operator"))
        except AccessDenied:
            raise HTTPException(404,"Workspace not found")
        return [dict(row) for row in db.execute("""SELECT id,actor_user_id,action,target_type,target_id,created_at
          FROM commercial_audit_events WHERE tenant_id=? ORDER BY created_at DESC,id DESC LIMIT 200""",(tenant_id,))]

class DraftLegCreate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1,max_length=100)
    selection: str = Field(min_length=1,max_length=200)
    outcome_id: str = Field(min_length=1,max_length=200)
    odds_milli: int = Field(gt=1000,le=10000000)
    stake_cents: int = Field(gt=0,le=100000000000)

@router.post("/workspaces/{tenant_id}/positions/{position_id}/legs",status_code=201)
def customer_add_draft_leg(tenant_id: str, position_id: str, body: DraftLegCreate, request: Request):
    return mutate(request,tenant_id,add_draft_leg,position_id,body.account_id,
                  body.selection,body.outcome_id,body.odds_milli,body.stake_cents)

@router.get("/workspaces/{tenant_id}/positions/{position_id}")
def customer_position_detail(tenant_id: str, position_id: str, request: Request):
    uid=current_user(request)
    with conn() as db:
        try:
            return draft_detail(db,uid,tenant_id,position_id)
        except AccessDenied:
            raise HTTPException(404,"Position not found")

@router.get("/workspaces/{tenant_id}/positions/{position_id}/validate")
def customer_validate_draft(tenant_id: str, position_id: str, request: Request):
    uid=current_user(request)
    with conn() as db:
        try:
            return validate_draft(db,uid,tenant_id,position_id)
        except AccessDenied:
            raise HTTPException(404,"Workspace not found")
        except ValueError as exc:
            raise HTTPException(422,str(exc))

@router.post("/workspaces/{tenant_id}/positions/{position_id}/cancel")
def customer_cancel_draft(tenant_id: str, position_id: str, request: Request):
    return mutate(request,tenant_id,cancel_draft,position_id)

@router.post("/workspaces/{tenant_id}/accounts/{account_id}/restrict")
def customer_restrict_account(tenant_id: str, account_id: str, request: Request):
    return mutate(request,tenant_id,restrict_account,account_id)
