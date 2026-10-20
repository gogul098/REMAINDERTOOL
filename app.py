"""Streamlit dashboard. Launch: streamlit run app.py --server.address 127.0.0.1"""
import csv
import io
import logging
import threading
from datetime import datetime, time, timedelta

import streamlit as st

from tracker import chrome, db, extractor, outbox
from tracker.config import ConfigError, load_config
from tracker.logsetup import setup_logging
from tracker.runner import run_once
from tracker.timeutil import countdown, format_local, iso_to_local, local_to_iso, now_utc

setup_logging()
log = logging.getLogger("tracker.app")
st.set_page_config(page_title="Course Deadlines", page_icon="📚", layout="wide")

try:
    cfg = load_config()
except ConfigError as e:
    st.error(f"Config problem: {e}")
    st.stop()


def conn():
    return db.connect(cfg.db_path)  # fresh connection per script run; Streamlit reruns on threads


def open_url(url: str) -> None:
    if not chrome.open_in_profile(cfg.chrome_exe, cfg.chrome_profile, url):
        st.session_state["flash"] = ("error", "chrome.exe not found; set chrome_exe in config.json")


def refresh_async() -> None:
    """Queue syncs, then update wallpaper, toasts and phone/widget in the background; the UI never waits on it."""
    c = conn()
    for target in ("gist", "ntfy"):
        outbox.enqueue(c, target)
    c.close()
    threading.Thread(target=run_once, args=(cfg, 15), daemon=True).start()


def flash(kind: str, msg: str) -> None:
    st.session_state["flash"] = (kind, msg)


# ---- callbacks (run before the rerun, so the UI always renders post-save state) ----
def do_sync():
    try:
        r = extractor.sync_bookmarks(conn(), cfg)
        flash("success", f"Synced: {r['seen']} bookmarks, {r['missing']} newly missing")
    except Exception as e:  # noqa: BLE001 - surface any failure to the user
        log.exception("sync failed")
        flash("error", f"Sync failed: {e}")


def do_activate(task_id: int):
    c = conn()
    title = st.session_state[f"title_{task_id}"].strip()
    d, t = st.session_state[f"date_{task_id}"], st.session_state[f"time_{task_id}"]
    with c:
        if title:
            db.set_title(c, task_id, title)
        db.activate(c, task_id, local_to_iso(datetime.combine(d, t), cfg.tz))
    refresh_async()


def act(fn, task_id: int, *args):
    c = conn()
    with c:
        fn(c, task_id, *args)
    refresh_async()


# ---- header ----
st.title("📚 Course Deadlines")
msg = st.session_state.pop("flash", None)
if msg:
    getattr(st, msg[0])(msg[1])

top = st.columns([1, 1, 1, 2])
top[0].button("🔄 Sync bookmarks", on_click=do_sync)
show_missing = top[1].toggle("Show removed bookmarks", value=False)
_pending = outbox.pending_count(conn())
top[2].markdown("✅ all synced" if not _pending else f"⏳ {_pending} pending")

if "synced_once" not in st.session_state:
    st.session_state["synced_once"] = True
    if cfg.bookmarks_files:
        do_sync()
        msg = st.session_state.pop("flash", None)
        if msg:
            getattr(st, msg[0])(msg[1])
    else:
        st.warning(f"No Bookmarks or AccountBookmarks file in {cfg.chrome_user_data_dir / cfg.chrome_profile}. Check chrome_profile in config.json.")

with st.sidebar:
    st.subheader("Chrome profiles")
    try:
        for p in extractor.list_chrome_profiles(cfg.chrome_user_data_dir):
            mark = "✅ " if p["folder"] == cfg.chrome_profile else ""
            st.write(f"{mark}**{p['folder']}**: {p['name']} {p['email']}")
    except Exception as e:  # noqa: BLE001
        st.caption(f"Could not read profiles: {e}")
    st.caption(f"Folders synced: {cfg.bookmark_folders}")
    try:
        with st.expander("Available folders"):
            st.code("\n".join(extractor.list_folders(*[extractor.read_bookmarks_file(f) for f in cfg.bookmarks_files])))
    except Exception:  # noqa: BLE001
        pass
    st.caption(f"Timezone: {cfg.timezone}")

c = conn()
tab_un, tab_act, tab_done = st.tabs(["Unscheduled", "Active", "Completed / Ignored"])

# ---- Unscheduled ----
with tab_un:
    rows = db.list_tasks(c, "UNSCHEDULED", show_missing)
    cats = sorted({r["category"] for r in rows})
    pick = st.multiselect("Filter by folder", cats)
    rows = [r for r in rows if not pick or r["category"] in pick]
    st.caption(f"{len(rows)} unscheduled")
    default_dt = datetime.now(cfg.tz) + timedelta(days=7)
    for r in rows:
        i = r["id"]
        with st.container(border=True):
            st.caption(r["category"] + ("  ·  ⚠️ removed from Chrome" if r["source_missing"] else ""))
            a, b, d, e = st.columns([4, 1.5, 1.2, 1.6])
            a.text_input("Title", value=r["title"], key=f"title_{i}", label_visibility="collapsed")
            b.date_input("Date", value=default_dt.date(), key=f"date_{i}", label_visibility="collapsed")
            d.time_input("Time", value=time(23, 59), key=f"time_{i}", label_visibility="collapsed", step=900)
            e.button("Activate", key=f"act_{i}", on_click=do_activate, args=(i,), type="primary")
            e.button("Ignore", key=f"ign_{i}", on_click=act, args=(db.ignore, i))
            st.caption(r["url"])

# ---- Active ----
with tab_act:
    rows = db.list_tasks(c, "ACTIVE", True)
    st.caption(f"{len(rows)} active, soonest first")
    now = now_utc()
    for r in rows:
        i = r["id"]
        left = (iso_to_local(r["deadline_utc"], cfg.tz) - now.astimezone(cfg.tz)).total_seconds()
        icon = "🔴" if left < 86400 else "🟠" if left < 3 * 86400 else "🟢"
        with st.container(border=True):
            a, b, d = st.columns([5, 3, 4])
            a.markdown(f"**{r['title']}**  \n{r['category']}" + ("  \n⚠️ removed from Chrome" if r["source_missing"] else ""))
            b.markdown(f"{icon} **{countdown(r['deadline_utc'])}**  \n{format_local(r['deadline_utc'], cfg.tz)}")
            d1, d2, d3, d4 = d.columns(4)
            d1.button("Open", key=f"open_{i}", on_click=open_url, args=(r["url"],))
            d2.button("+1 day", key=f"ext_{i}", on_click=act, args=(db.extend, i, 1))
            d3.button("Done", key=f"done_{i}", on_click=act, args=(db.complete, i), type="primary")
            d4.button("Unschedule", key=f"uns_{i}", on_click=act, args=(db.unschedule, i))

# ---- Completed / Ignored ----
with tab_done:
    for status in ("COMPLETED", "IGNORED"):
        rows = db.list_tasks(c, status, True)
        st.subheader(f"{status.title()} ({len(rows)})")
        for r in rows:
            i = r["id"]
            a, b = st.columns([8, 1])
            a.write(f"{r['title']}  ·  _{r['category']}_")
            b.button("Undo", key=f"undo_{i}", on_click=act, args=(db.restore, i))

# ---- CSV export ----
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["title", "category", "url", "status", "deadline_local", "deadline_utc"])
for r in c.execute("SELECT * FROM tasks ORDER BY status, deadline_utc"):
    local = format_local(r["deadline_utc"], cfg.tz) if r["deadline_utc"] else ""
    w.writerow([r["title"], r["category"], r["url"], r["status"], local, r["deadline_utc"] or ""])
st.sidebar.download_button("⬇️ Export CSV", buf.getvalue(), "deadlines.csv", "text/csv")
