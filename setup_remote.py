"""One-time setup for phone/widget sync. Safe to re-run.

1. Generates a long random ntfy topic into config.json (if none yet).
2. Creates the secret Gist (if you have a GITHUB_TOKEN and no gist_id yet) and prints the widget URL.
"""
import json
import secrets as stdlib_secrets
import string

from tracker import mobile
from tracker.config import ROOT, load_config
from tracker.credentials import SERVICE, get_secret

path = ROOT / "config.json"
raw = json.loads(path.read_text(encoding="utf-8"))
raw.setdefault("ntfy", {})
if not raw["ntfy"].get("topic"):
    alphabet = string.ascii_lowercase + string.digits
    raw["ntfy"]["topic"] = "deadlines-" + "".join(stdlib_secrets.choice(alphabet) for _ in range(24))
    raw["ntfy"].setdefault("server", "https://ntfy.sh")
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    print("Generated ntfy topic and saved it to config.json")
cfg = load_config()
print(f"\nntfy: in the ntfy Android app, subscribe to topic:\n  {cfg.ntfy['topic']}  (server {cfg.ntfy['server']})")

token, state = get_secret("GITHUB_TOKEN"), mobile.load_state()
if not token:
    print(f"\nGist: no GITHUB_TOKEN found. Create a fine-grained token with only the 'Gists: read and write' "
          f"permission, then store it with:\n  .\\.venv\\Scripts\\python -m keyring set {SERVICE} GITHUB_TOKEN\n"
          f"(or put GITHUB_TOKEN=... in .env) and re-run this script.")
elif not state.get("gist_id"):
    gist = mobile.create_gist(cfg, token)
    mobile.save_state({**state, "gist_id": gist["id"], "gist_hash": None})
    print(f"\nCreated secret Gist {gist['id']}")
    state = mobile.load_state()
if token and state.get("gist_id"):
    import requests

    g = requests.get(f"{mobile.API}/gists/{state['gist_id']}", headers=mobile._headers(token), timeout=15).json()
    print(f"\nWidget URL (stable, always latest):\n  {mobile.raw_url(g, cfg.mobile['gist_filename'])}")
