"""One toast, in its own process. Blocks until the toast is clicked/dismissed so on_click can run.

Usage: python -m tracker.toast_worker TITLE BODY URL PROFILE [CHROME_EXE]
"""
import os
import sys
import threading

from win11toast import toast

from .chrome import open_in_profile


def main(title: str, body: str, url: str, profile: str, exe: str = "") -> None:
    threading.Timer(3600, lambda: os._exit(0)).start()  # never linger forever

    def on_click(args):
        pressed = str(args.get("arguments", "")) if isinstance(args, dict) else ""
        if not pressed.endswith("Dismiss"):  # body click or the Open button
            open_in_profile(exe or None, profile, url)

    toast(title, body, buttons=["Open", "Dismiss"], on_click=on_click, scenario="reminder")


if __name__ == "__main__":
    main(*sys.argv[1:6])
