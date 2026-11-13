"""Additive schema for the future customer platform.

This migration deliberately does NOT expose customer routes or retrofit legacy
positions: the existing Hunta/Jay operator database remains separate until all
queries and writes can be scoped atomically. Never mark tenant isolation complete
based on these tables alone.
"""
import sqlite3


def migrate_customer_platform(con: sqlite3.Connection) -> None:
    """Create future tenant identity and ledger tables without moving legacy data."""
    con.executescript("""
        CREATE TABLE IF NOT EXISTS customer_tenants (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL CHECK(length(trim(display_name)) > 0),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','active','suspended','closed')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS customer_users (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES customer_tenants(id) ON DELETE RESTRICT,
            email TEXT NOT NULL COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member'
                CHECK(role IN ('owner','member')),
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
            failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempts >= 0),
            locked_until TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, email),
            UNIQUE(tenant_id, id)
        );

        CREATE INDEX IF NOT EXISTS idx_customer_users_tenant
        ON customer_users(tenant_id, active);

        CREATE TABLE IF NOT EXISTS customer_sessions (
            token_hash TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tenant_id, user_id)
                REFERENCES customer_users(tenant_id, id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_customer_sessions_expiry
        ON customer_sessions(expires_at);

        CREATE TABLE IF NOT EXISTS customer_bookmaker_accounts (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES customer_tenants(id) ON DELETE RESTRICT,
            bookmaker TEXT NOT NULL,
            label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active','restricted','closed')),
            opening_balance_cents INTEGER NOT NULL DEFAULT 0
                CHECK(opening_balance_cents >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tenant_id, id),
            UNIQUE(tenant_id, bookmaker, label)
        );

        CREATE INDEX IF NOT EXISTS idx_customer_accounts_tenant
        ON customer_bookmaker_accounts(tenant_id, status);

        CREATE TABLE IF NOT EXISTS customer_positions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES customer_tenants(id) ON DELETE RESTRICT,
            event_id TEXT NOT NULL,
            market_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft','approved','partially_placed','open',
                                 'settled','cancelled')),
            total_stake_cents INTEGER NOT NULL DEFAULT 0
                CHECK(total_stake_cents >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            settled_at TEXT,
            UNIQUE(tenant_id, id)
        );

        CREATE INDEX IF NOT EXISTS idx_customer_positions_tenant_status
        ON customer_positions(tenant_id, status, created_at);

        CREATE TABLE IF NOT EXISTS customer_position_legs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            position_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            outcome_id TEXT NOT NULL,
            odds REAL NOT NULL CHECK(odds > 1),
            stake_cents INTEGER NOT NULL CHECK(stake_cents > 0),
            accepted INTEGER NOT NULL DEFAULT 0 CHECK(accepted IN (0,1)),
            settled INTEGER NOT NULL DEFAULT 0 CHECK(settled IN (0,1)),
            FOREIGN KEY(tenant_id, position_id)
                REFERENCES customer_positions(tenant_id, id) ON DELETE RESTRICT,
            FOREIGN KEY(tenant_id, account_id)
                REFERENCES customer_bookmaker_accounts(tenant_id, id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_customer_legs_position
        ON customer_position_legs(tenant_id, position_id);

        CREATE TABLE IF NOT EXISTS customer_bankroll_entries (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES customer_tenants(id) ON DELETE RESTRICT,
            account_id TEXT,
            amount_cents INTEGER NOT NULL,
            entry_type TEXT NOT NULL,
            reference_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tenant_id, account_id)
                REFERENCES customer_bookmaker_accounts(tenant_id, id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_customer_bankroll_tenant
        ON customer_bankroll_entries(tenant_id, created_at);

        CREATE TABLE IF NOT EXISTS customer_audit_log (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES customer_tenants(id) ON DELETE RESTRICT,
            actor_user_id TEXT,
            action TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tenant_id, actor_user_id)
                REFERENCES customer_users(tenant_id, id) ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_customer_audit_tenant
        ON customer_audit_log(tenant_id, created_at);
    """)
