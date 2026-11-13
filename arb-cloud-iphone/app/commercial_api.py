"""Tenant-isolated customer API. Legacy routes are internal-only.

Public signup is intentionally disabled pending email verification and billing.
Provision users through a controlled administrative workflow.
"""
import os
import secrets
import sqlite3
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from app.database import get_db
from app.commercial_security import (
    AccessDenied, authenticate_limited, create_session, resolve_session, revoke_session,
    require_member, tenant_rows,
)
from app.commercial_schema import migrate_commercial_schema, migrate_legacy_tenant_columns

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

@router.post("/logout")
def customer_logout(request: Request, response: Response):
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
