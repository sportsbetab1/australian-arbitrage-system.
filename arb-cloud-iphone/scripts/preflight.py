"""Read-only host checks; JSON output never includes secret values or row data."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import time


def inspect_host(db_path, backup_dir=None, environ=None, timestamp=None):
    env = os.environ if environ is None else environ
    instant = time.time() if timestamp is None else timestamp
    checks = []

    def add(name, state, detail):
        checks.append({"name": name, "state": state, "detail": detail})

    add("secure_cookies", "pass" if env.get("AUTH_COOKIE_SECURE", "true").lower() == "true" else "fail",
        "HTTPS hosting requires secure cookies.")
    source = Path(db_path).resolve()
    users = None
    if not source.is_file():
        add("database", "fail", "Existing database is missing; start the application first.")
    else:
        try:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=5)) as con:
                if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise sqlite3.DatabaseError()
                if con.execute("PRAGMA foreign_key_check").fetchone():
                    raise sqlite3.DatabaseError()
                for table in ("positions", "position_legs", "account_transactions", "audit_log",
                              "push_delivery_queue", "request_budgets", "auth_sessions"):
                    con.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchall()
                users = con.execute("SELECT COUNT(*) FROM auth_users WHERE is_active = 1").fetchone()[0]
                dead = con.execute("SELECT COUNT(*) FROM push_delivery_queue WHERE status = 'dead'").fetchone()[0]
            add("database", "pass", "Database schema, quick integrity and foreign-key checks passed.")
            add("push_dead_letters", "warn" if dead else "pass", "Review dead letters in the private dashboard." if dead else "No dead letters.")
        except sqlite3.Error:
            add("database", "fail", "Database is unreadable, incomplete, or failed integrity checks.")
    if users is not None:
        add("accounts", "pass" if users == 2 else "fail", "Both owner accounts must be active before release.")
        if users == 0:
            token = env.get("AUTH_SETUP_TOKEN", "")
            add("setup_token", "pass" if len(token) >= 32 and not token.startswith("replace-") else "fail",
                "First-run setup requires a long random token in private host settings.")

    directory = Path(backup_dir).resolve() if backup_dir else source.parent / "backups"
    try:
        snapshots = sorted(directory.glob("arbitrage-*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not snapshots:
            add("backup", "fail", "No local snapshot found.")
        else:
            latest = snapshots[0]
            age = instant - latest.stat().st_mtime
            with closing(sqlite3.connect(latest.as_uri() + "?mode=ro", uri=True)) as con:
                valid = con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            add("backup", "pass" if valid and 0 <= age <= 26 * 3600 else "fail",
                "Latest local snapshot must pass quick integrity and be less than 26 hours old.")
    except (OSError, sqlite3.Error):
        add("backup", "fail", "Latest local snapshot could not be read or verified.")

    add("odds", "pass" if env.get("ODDS_API_KEY") else "warn", "Odds provider key configured; live access is unverified." if env.get("ODDS_API_KEY") else "Live odds disabled until a provider key is configured.")
    push = all(env.get(key) for key in ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"))
    add("push_configuration", "pass" if push else "warn", "Push settings present; delivery and scheduler are unverified." if push else "Push settings incomplete; phone delivery is disabled.")
    add("external_operations", "warn", "Manually verify HTTPS/proxy trust, edge limits, timers, monitoring, off-site backups and a host restore drill.")
    return {"ok": not any(item["state"] == "fail" for item in checks), "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("DB_PATH", "./data/arbitrage.db"))
    parser.add_argument("--backup-dir", default=os.getenv("BACKUP_DIR"))
    args = parser.parse_args()
    result = inspect_host(args.db, args.backup_dir)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
