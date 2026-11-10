import os
import sqlite3
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "./data/arbitrage.db")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()

    conn.executescript("""

    CREATE TABLE IF NOT EXISTS opportunities (
        id TEXT PRIMARY KEY, created_at TEXT, sport TEXT, event TEXT, market TEXT,
        outcomes_json TEXT, sum_inverse REAL, roi REAL, stake_json TEXT, status TEXT
    );

    CREATE TABLE IF NOT EXISTS sets (
        id TEXT PRIMARY KEY, opportunity_id TEXT, created_at TEXT, sport TEXT,
        event TEXT, market TEXT, total_stake REAL, roi REAL, expected_profit REAL,
        status TEXT, actual_pnl REAL, notes TEXT
    );

    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        subscription_json TEXT UNIQUE
    );

    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner TEXT NOT NULL CHECK(owner IN ('Hunta', 'Jay')),
        bookmaker TEXT NOT NULL,
        opening_balance REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(owner, bookmaker)
    );

    CREATE TABLE IF NOT EXISTS bankroll_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner TEXT,
        entry_type TEXT NOT NULL,
        amount REAL NOT NULL,
        description TEXT,
        reference_id INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event TEXT NOT NULL,
        sport TEXT NOT NULL,
        market TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        total_stake REAL NOT NULL DEFAULT 0,
        expected_profit REAL NOT NULL DEFAULT 0,
        expected_roi REAL NOT NULL DEFAULT 0,
        manual_confirmed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        settled_at TEXT,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS position_legs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        position_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        selection TEXT NOT NULL,
        odds REAL NOT NULL,
        stake REAL NOT NULL,
        accepted INTEGER NOT NULL DEFAULT 0,
        settled INTEGER NOT NULL DEFAULT 0,
        actual_return REAL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(position_id) REFERENCES positions(id),
        FOREIGN KEY(account_id) REFERENCES accounts(id)
    );

    CREATE TABLE IF NOT EXISTS buffer_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        position_id INTEGER,
        amount REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'committed',
        due_at TEXT NOT NULL,
        resolved_at TEXT,
        description TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(position_id) REFERENCES positions(id)
    );

    CREATE TABLE IF NOT EXISTS profit_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        position_id INTEGER,
        realised_profit REAL NOT NULL,
        reinvested_amount REAL NOT NULL,
        withdrawal_amount REAL NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(position_id) REFERENCES positions(id)
    );

    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)

    bookmakers = [
        "Sportsbet",
        "Betfair",
        "PointsBet",
        "Unibet",
        "Dabble",
        "Picklebet",
        "Ladbrokes"
    ]

    for owner in ("Hunta", "Jay"):
        for bookmaker in bookmakers:
            conn.execute(
                """
                INSERT OR IGNORE INTO accounts
                (owner, bookmaker, opening_balance)
                VALUES (?, ?, 0)
                """,
                (owner, bookmaker)
            )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database initialised.")
def migrate_position_schema():
    conn = get_db()

    migrations = {
        "positions": {
            "event_id": "TEXT",
            "market_id": "TEXT",
            "market_type": "TEXT",
            "possible_outcomes_json": "TEXT",
            "market_rules_verified": "INTEGER NOT NULL DEFAULT 0",
        },
        "position_legs": {
            "event_id": "TEXT",
            "market_id": "TEXT",
            "outcome_id": "TEXT",
            "commission_pct": "REAL NOT NULL DEFAULT 0",
        },
    }

    try:
        for table, columns in migrations.items():
            existing = {
                row["name"]
                for row in conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }

            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} "
                        f"ADD COLUMN {column} {definition}"
                    )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()
