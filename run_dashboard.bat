@echo off
cd /d "%~dp0"
".venv\Scripts\streamlit.exe" run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
