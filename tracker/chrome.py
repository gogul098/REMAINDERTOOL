"""Locate Chrome and open URLs in the configured profile."""
import shutil
import subprocess
from pathlib import Path

CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def chrome_path(configured: str | None = None) -> str | None:
    if configured:
        return configured
    for p in CANDIDATES:
        if Path(p).exists():
            return p
    return shutil.which("chrome")


def open_in_profile(exe: str | None, profile: str, url: str) -> bool:
    exe = chrome_path(exe)
    if not exe:
        return False
    subprocess.Popen([exe, f"--profile-directory={profile}", url])
    return True
