from pathlib import Path
import re


source = Path("static/index.html").read_text(encoding="utf-8")
match = re.search(r"<script>(.*?)</script>", source, flags=re.DOTALL)
if not match:
    raise SystemExit("Dashboard script block was not found.")
Path("/tmp/arbitrage-dashboard.js").write_text(match.group(1), encoding="utf-8")
print("Dashboard script extracted.")
