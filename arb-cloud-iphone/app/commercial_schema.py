"""Additive commercial identity and tenancy schema (not activated by default).

Run only after backup and review. This migration does not change existing
auth_users, accounts, positions or betting execution paths.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS commercial_tenants (
 id TEXT PRIMARY KEY,
 display_name TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('internal','customer')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','suspended','closed')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS commercial_users (
 id TEXT PRIMARY KEY,
 email_normalized TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 email_verified_at TEXT,
 disabled_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(length(email_normalized) > 3)
);
CREATE TABLE IF NOT EXISTS commercial_memberships (
 tenant_id TEXT NOT NULL REFERENCES commercial_tenants(id) ON DELETE RESTRICT,
 user_id TEXT NOT NULL REFERENCES commercial_users(id) ON DELETE RESTRICT,
 role TEXT NOT NULL CHECK(role IN ('platform_owner','tenant_admin','operator','viewer')),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('invited','active','revoked')),
 PRIMARY KEY (tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS idx_commercial_memberships_user ON commercial_memberships(user_id);
CREATE TABLE IF NOT EXISTS commercial_sessions (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES commercial_users(id) ON DELETE CASCADE,
 token_hash TEXT NOT NULL UNIQUE,
 expires_at TEXT NOT NULL,
 revoked_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_commercial_sessions_user ON commercial_sessions(user_id);
CREATE TABLE IF NOT EXISTS commercial_plans (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 monthly_price_cents INTEGER NOT NULL CHECK(monthly_price_cents >= 0),
 currency TEXT NOT NULL DEFAULT 'AUD',
 active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS commercial_subscriptions (
 id TEXT PRIMARY KEY,
 tenant_id TEXT NOT NULL REFERENCES commercial_tenants(id) ON DELETE RESTRICT,
 plan_id TEXT NOT NULL REFERENCES commercial_plans(id) ON DELETE RESTRICT,
 provider_customer_id TEXT,
 provider_subscription_id TEXT UNIQUE,
 status TEXT NOT NULL CHECK(status IN ('trialing','active','past_due','cancelled','expired')),
 current_period_end TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_commercial_subscriptions_tenant ON commercial_subscriptions(tenant_id);
CREATE TABLE IF NOT EXISTS commercial_profit_fee_ledger (
 id TEXT PRIMARY KEY,
 tenant_id TEXT NOT NULL REFERENCES commercial_tenants(id) ON DELETE RESTRICT,
 position_external_id TEXT NOT NULL,
 realised_profit_cents INTEGER NOT NULL,
 fee_basis_cents INTEGER NOT NULL CHECK(fee_basis_cents >= 0),
 fee_rate_bps INTEGER NOT NULL DEFAULT 1000 CHECK(fee_rate_bps BETWEEN 0 AND 10000),
 fee_amount_cents INTEGER NOT NULL CHECK(fee_amount_cents >= 0),
 status TEXT NOT NULL DEFAULT 'disabled' CHECK(status IN ('disabled','pending_review','approved','void')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(tenant_id, position_external_id)
);
CREATE TABLE IF NOT EXISTS commercial_audit_events (
 id TEXT PRIMARY KEY,
 tenant_id TEXT REFERENCES commercial_tenants(id) ON DELETE RESTRICT,
 actor_user_id TEXT REFERENCES commercial_users(id) ON DELETE SET NULL,
 action TEXT NOT NULL,
 target_type TEXT NOT NULL,
 target_id TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_commercial_audit_tenant ON commercial_audit_events(tenant_id,created_at);
"""

def migrate_commercial_schema(conn: sqlite3.Connection) -> None:
    """Idempotent additive migration. Caller controls transaction and backup."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
