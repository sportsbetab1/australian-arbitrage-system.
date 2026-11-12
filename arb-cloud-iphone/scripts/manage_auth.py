"""Host-administrator recovery; passwords are entered only at an interactive prompt."""
import argparse
from contextlib import closing
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
import warnings

from argon2 import PasswordHasher


def manage_user(db_path, username, reason, *, new_password=None):
    username = username.strip().lower()
    reason = reason.strip()
    if username not in {"hunta", "jay"}:
        raise ValueError("Choose an existing Hunta or Jay account.")
    if not reason or len(reason) > 500:
        raise ValueError("Enter an administrative reason of 1 to 500 characters.")
    if new_password is not None and (not isinstance(new_password, str) or not 12 <= len(new_password) <= 256):
        raise ValueError("The new password must contain 12 to 256 characters.")
    source = Path(db_path).resolve()
    if not source.is_file():
        raise FileNotFoundError("The existing application database was not found.")

    # mode=rw prevents a typo from creating an empty replacement database.
    with closing(sqlite3.connect(source.as_uri() + "?mode=rw", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        row = con.execute("SELECT id FROM auth_users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise ValueError("The account does not exist; complete initial setup first.")
        password_hash = PasswordHasher().hash(new_password) if new_password is not None else None
        with con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT id, is_active FROM auth_users WHERE username = ?", (username,)).fetchone()
            if row is None:
                raise ValueError("The account no longer exists.")
            if password_hash is not None:
                con.execute("""
                    UPDATE auth_users SET password_hash = ?, failed_attempts = 0,
                        locked_until = NULL WHERE id = ?
                """, (password_hash, row["id"]))
            revoked = con.execute("DELETE FROM auth_sessions WHERE user_id = ?", (row["id"],)).rowcount
            action = "auth_password_reset" if password_hash is not None else "auth_sessions_revoked"
            con.execute("INSERT INTO audit_log(action, details) VALUES (?, ?)", (action, json.dumps({
                "user_id": row["id"], "username": username, "sessions_revoked": revoked,
                "reason": reason, "recorded_by": "host_administrator",
            })))
    return {"username": username, "sessions_revoked": revoked, "is_active": bool(row["is_active"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recover existing application accounts using host database access.")
    parser.add_argument("action", choices=["reset-password", "revoke-sessions"])
    parser.add_argument("--db", default=os.getenv("DB_PATH", "./data/arbitrage.db"))
    parser.add_argument("--user", required=True, choices=["hunta", "jay"])
    parser.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    password = None
    try:
        if args.action == "reset-password":
            if not sys.stdin.isatty():
                raise ValueError("Password reset requires an interactive terminal; passwords cannot be piped or passed as arguments.")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("New password (12–256 characters): ")
                repeated = getpass.getpass("Repeat new password: ")
            if password != repeated:
                raise ValueError("Passwords did not match; no changes were made.")
        result = manage_user(args.db, args.user, args.reason, new_password=password)
    except (ValueError, OSError, sqlite3.Error, getpass.GetPassWarning, EOFError) as error:
        parser.exit(1, f"Recovery failed: {error}\n")
    action = "Password reset" if args.action == "reset-password" else "Sessions revoked"
    print(f"{action} for {result['username']}; revoked {result['sessions_revoked']} session(s).")
    if not result["is_active"]:
        print("The account remains inactive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
