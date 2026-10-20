# Course Deadline Tracker

Turns the bookmarks in one Chrome profile into deadlines you set in a local dashboard. Deadlines appear on the
Windows wallpaper, as Windows toasts, as ntfy push alerts on Android, and in an Android home-screen widget.
SQLite (`data/courses.db`) is the single source of truth; every outbound sync is idempotent and retried from an outbox.

## Setup

1. `powershell -ExecutionPolicy Bypass -File .\install.ps1` (venv, scheduled task, desktop shortcut)
2. Edit `config.json`: `chrome_profile`, `bookmark_folders`, `timezone`.
3. `.\.venv\Scripts\python setup_remote.py` generates the ntfy topic and creates the secret Gist.
   Subscribe to the printed topic in the ntfy Android app; point the widget (KWGT / HTTP Request Widget) at the printed URL.
4. Secrets (GitHub token with only *Gists: read and write*, optional ntfy token) go in Credential Manager:
   `.\.venv\Scripts\python -m keyring set course-deadline-tracker GITHUB_TOKEN` (or a git-ignored `.env`).

Open the dashboard with the "Course Deadlines" shortcut, or `run_dashboard.bat`.

## Phone app (installable web app)

`docs/` is a small installable web app: live countdowns, colour-coded cards, works offline from its last data.
Turn it on once: repo Settings > Pages > Source "Deploy from a branch", branch `main`, folder `/docs`.
Then open https://gogul098.github.io/REMAINDERTOOL/ in Chrome on the phone, paste the deadlines link when asked
(it is stored only on the phone, never in the repo), and use menu > "Add to Home screen".
Set `ntfy.app_url` (and optionally `ntfy.icon_url`) in `config.json` so alerts get an "Open tracker" button.

## Alerts when the laptop is off (optional relay)

ntfy can schedule at most 3 days ahead, so the laptop schedules what fits and tops up on each hourly run. To keep
topping up while the laptop is off, push this folder to a GitHub repository and add these Actions secrets
(Settings > Secrets and variables > Actions):

| Secret | Value |
|---|---|
| `DEADLINES_URL` | the widget URL printed by `setup_remote.py` |
| `NTFY_TOPIC` | the topic in `config.json` |
| `NTFY_SERVER` | optional, default `https://ntfy.sh` |
| `NTFY_TOKEN` | optional |
| `APP_URL` | optional, the Pages URL; adds an "Open tracker" button to relay alerts |

`.github/workflows/relay.yml` runs hourly. `config.json`, `.env` and `data/` are git-ignored, so none of your
data or secrets is in the repo. GitHub pauses scheduled workflows after 60 days without repo activity; re-enable
from the Actions tab if that happens. The relay only schedules; completing or extending a task still cancels or
replaces alerts from the laptop, so do that while online.

## Operations

- Logs: `logs/tracker.log` (rotating, 1 MB x 5). Backups: `data/backups/`, daily, newest 14 kept.
- Tests: `.\.venv\Scripts\python -m pytest`
- Uninstall: `powershell -ExecutionPolicy Bypass -File .\uninstall.ps1` (removes the task, restores your wallpaper).

## Limits

Windows must be set to "Picture" (not Slideshow or Spotlight); the lock screen is untouched. Secret Gists are
unlisted, not private, so the payload is only titles and URLs. A toast can only be clicked while it is still on screen.
