"""Secrets come from the environment, a git-ignored .env, or Windows Credential Manager (in that order)."""
import os

from .config import ROOT

SERVICE = "course-deadline-tracker"


def _dotenv() -> dict[str, str]:
    f = ROOT / ".env"
    out: dict[str, str] = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    return out


def get_secret(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    val = _dotenv().get(name)
    if val:
        return val
    try:
        import keyring

        return keyring.get_password(SERVICE, name) or None
    except Exception:  # noqa: BLE001 - no usable keyring backend
        return None
