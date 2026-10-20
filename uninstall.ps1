# Removes the scheduled task and restores the wallpaper you had before the tracker first changed it.
$root = $PSScriptRoot
Unregister-ScheduledTask -TaskName "CourseDeadlineTracker" -Confirm:$false -ErrorAction SilentlyContinue
& "$root\.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'$root'); from tracker import wallpaper; print('restored' if wallpaper.restore_original() else 'no saved original wallpaper')"
