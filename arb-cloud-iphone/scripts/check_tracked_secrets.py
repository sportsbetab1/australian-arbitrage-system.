"""Fail CI if local credentials or database dumps have been added to Git."""
from pathlib import PurePosixPath
import subprocess
import sys

ALLOW = {".env.example", ".env.sample", ".env.template"}
SENSITIVE = (
    ".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db",
    ".dump", ".backup", ".sql.gz",
)

def forbidden(path):
    name = PurePosixPath(path).name.lower()
    return (
        (name == ".env" or name.startswith(".env.")) and name not in ALLOW
        or name.endswith(SENSITIVE)
    )

def main():
    output = subprocess.check_output(["git", "ls-files", "-z"])
    paths = [raw.decode("utf-8", errors="replace") for raw in output.split(b"\0") if raw]
    matches = [path for path in paths if forbidden(path)]
    if matches:
        print("Blocked tracked sensitive filenames (contents not displayed):")
        for path in matches:
            print(" -", path)
        return 1
    print(f"Checked {len(paths)} tracked files; no blocked filenames found.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
