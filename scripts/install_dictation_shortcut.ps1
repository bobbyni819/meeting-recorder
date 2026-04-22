# Install a Start Menu shortcut for `python -m meeting_recorder dictate`.
#
# Run once per Windows machine to make dictation launchable from the
# Start menu / Windows search bar. Uses python.exe (not pythonw.exe) so
# the console window shows recording/transcription status.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\install_dictation_shortcut.ps1

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python.exe -ErrorAction SilentlyContinue)?.Source
if (-not $python) {
    Write-Error "python.exe not found on PATH. Install Python or activate your env first."
}

$shortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Meeting Recorder Dictation.lnk"

$sh = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($shortcutPath)
$lnk.TargetPath = $python
$lnk.Arguments = "-m meeting_recorder dictate"
$lnk.WorkingDirectory = $repoRoot
$lnk.WindowStyle = 1
$lnk.Description = "Solo voice-memo dictation via Gemini (Ctrl+Shift+V to toggle)"
$lnk.IconLocation = "$python,0"
$lnk.Save()

Write-Host "Installed: $shortcutPath"
Write-Host "Target: $python -m meeting_recorder dictate"
Write-Host "Working dir: $repoRoot"
Write-Host ""
Write-Host "Open Windows search and type 'Meeting Recorder Dictation' to launch."
