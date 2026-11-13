"""Commercial auth and tenant-scoped data access. Not wired to legacy routes.

All tenant IDs come from validated membership, never from client-supplied
query parameters. Public activation requires route-by-route integration.
"""
import hashlib
import hmac
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

HASHER = PasswordHasher()
SESSION_HOURS = 12
SENSITIVE_ACTIONS = frozenset({"change_subscription_price", "change_profit_fee", "transfer_company_funds", "grant_platform_owner", "remove_platform_owner"})

class AccessDenied(Exception):
    pass

def utcnow():
    return datetime.now(timezone.utc)

def new_id():
    return str(uuid.uuid4())

def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def normalize_email(email):
    result = email.strip().casefold()
    if not result or len(result) > 254 or result.count("@") != 1:
        raise ValueError("Invalid email")
    return result

def create_user(con, email, password):
    if len(password) < 12 or len(password.encode("utf-8")) > 1024:
        raise ValueError("Password must be at least 12 characters")
    uid = new_id()
    con.execute("INSERT INTO commercial_users(id,email_normalized,password_hash) VALUES (?,?,?)",
                (uid, normalize_email(email), HASHER.hash(password)))
    return uid

def authenticate(con, email, password):
    row = con.execute("SELECT * FROM commercial_users WHERE email_normalized=? AND disabled_at IS NULL",
                      (normalize_email(email),)).fetchone()
    # Always do a password hash verification, even for an unknown email.
    stored = row["password_hash"] if row else HASHER.hash("invalid-login-dummy-password")
    try:
        valid = HASHER.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        valid = False
    if not row or not valid or not row["email_verified_at"]:
        raise AccessDenied("Invalid credentials or unverified email")
    return row["id"]

def create_session(con, user_id):
    token = secrets.token_urlsafe(48)
    expiry = (utcnow() + timedelta(hours=SESSION_HOURS)).isoformat()
    con.execute("INSERT INTO commercial_sessions(id,user_id,token_hash,expires_at) VALUES (?,?,?,?)",
                (new_id(), user_id, hash_token(token), expiry))
    return token

def resolve_session(con, token):
    if not token:
        raise AccessDenied("Authentication required")
    row = con.execute("""SELECT s.user_id, s.expires_at FROM commercial_sessions s
        JOIN commercial_users u ON u.id=s.user_id
        WHERE s.token_hash=? AND s.revoked_at IS NULL AND u.disabled_at IS NULL""",
        (hash_token(token),)).fetchone()
    if not row or datetime.fromisoformat(row["expires_at"]) <= utcnow():
        raise AccessDenied("Session expired or invalid")
    return row["user_id"]

def revoke_session(con, token):
    con.execute("UPDATE commercial_sessions SET revoked_at=? WHERE token_hash=?",
                (utcnow().isoformat(), hash_token(token)))

def require_member(con, user_id, tenant_id, roles=("tenant_admin","operator","viewer")):
    row = con.execute("""SELECT m.role FROM commercial_memberships m
      JOIN commercial_tenants t ON t.id=m.tenant_id
      WHERE m.user_id=? AND m.tenant_id=? AND m.status='active' AND t.status='active'""",
      (user_id, tenant_id)).fetchone()
    if not row or row["role"] not in roles:
        raise AccessDenied("Not authorized for this workspace")
    return row["role"]

def tenant_rows(con, user_id, tenant_id, table):
    # Strict allowlist: table names cannot be supplied freely.
    allowed = {"accounts", "positions", "bankroll_ledger"}
    if table not in allowed:
        raise ValueError("Unknown resource")
    require_member(con, user_id, tenant_id)
    return con.execute(f"SELECT * FROM {table} WHERE tenant_id=? ORDER BY id",
                       (tenant_id,)).fetchall()

def require_platform_owner(con, user_id):
    row = con.execute("""SELECT 1 FROM commercial_memberships m
      JOIN commercial_tenants t ON t.id=m.tenant_id
      WHERE m.user_id=? AND m.role='platform_owner' AND m.status='active'
      AND t.kind='internal' AND t.status='active'""", (user_id,)).fetchone()
    if not row:
        raise AccessDenied("Platform owner required")

def request_sensitive_action(con, actor_id, action, payload_digest):
    require_platform_owner(con, actor_id)
    if action not in SENSITIVE_ACTIONS:
        raise ValueError("Action not eligible for dual approval")
    aid = new_id()
    con.execute("""INSERT INTO commercial_owner_approvals
      (id,action,payload_digest,requested_by,expires_at)
      VALUES (?,?,?,?,?)""",
      (aid, action, payload_digest, actor_id, (utcnow()+timedelta(hours=24)).isoformat()))
    return aid

def approve_sensitive_action(con, approver_id, approval_id):
    require_platform_owner(con, approver_id)
    row = con.execute("SELECT * FROM commercial_owner_approvals WHERE id=?", (approval_id,)).fetchone()
    if not row or row["status"] != "pending" or row["requested_by"] == approver_id:
        raise AccessDenied("Independent pending approval required")
    if datetime.fromisoformat(row["expires_at"]) <= utcnow():
        raise AccessDenied("Approval expired")
    # Approval is not execution. A separate transactional executor must verify
    # action, digest, approved status and single-use consumption.
    con.execute("""UPDATE commercial_owner_approvals
       SET approved_by=?, status='approved', approved_at=?
       WHERE id=? AND status='pending' AND approved_by IS NULL""",
       (approver_id, utcnow().isoformat(), approval_id))
    if con.execute("SELECT changes()").fetchone()[0] != 1:
        raise AccessDenied("Already processed")
