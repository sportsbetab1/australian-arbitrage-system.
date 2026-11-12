from pathlib import Path
import re


source = Path("static/index.html").read_text(encoding="utf-8")
if not source.rstrip().endswith("</html>") or source.count("</html>") != 1:
    raise SystemExit("Dashboard must end at its single closing HTML tag.")
match = re.search(r"<script>(.*?)</script>", source, flags=re.DOTALL)
if not match:
    raise SystemExit("Dashboard script block was not found.")
Path("/tmp/arbitrage-dashboard.js").write_text(match.group(1), encoding="utf-8")
print("Dashboard script extracted.")

