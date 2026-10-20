# Sets up the venv, installs pinned deps, registers the Task Scheduler job, and drops a dashboard launcher.
# Run with:  powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$taskName = "CourseDeadlineTracker"

$ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ([version]$ver -lt [version]"3.11") { throw "Python 3.11+ is required (found $ver)." }

if (-not (Test-Path "$root\.venv")) { python -m venv "$root\.venv" }
$req = if (Test-Path "$root\requirements.lock") { "requirements.lock" } else { "requirements.txt" }
& "$root\.venv\Scripts\python.exe" -m pip install -q -r "$root\$req"
if (-not (Test-Path "$root\config.json")) { Copy-Item "$root\config.example.json" "$root\config.json" }
foreach ($d in "data", "logs", "output") { New-Item -ItemType Directory -Force "$root\$d" | Out-Null }

$pythonw = "$root\.venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "daemon.py" -WorkingDirectory $root
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($logon, $hourly) `
    -Settings $settings -Description "Course deadline wallpaper, toasts and phone sync" -Force | Out-Null

# Desktop shortcut that opens the dashboard on 127.0.0.1 only
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Course Deadlines.lnk"
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sh.TargetPath = "$root\run_dashboard.bat"
$sh.WorkingDirectory = $root
$sh.Save()

Write-Host "Installed task '$taskName' and a 'Course Deadlines' desktop shortcut."
Write-Host "Next: python setup_remote.py (phone + widget), then Start-ScheduledTask -TaskName $taskName"
