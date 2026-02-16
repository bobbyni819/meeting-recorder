@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo   Meeting Recorder - Setup
echo ============================================================
echo.

:: Check for Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+ from:
    echo   https://www.python.org/downloads/
    echo   Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: Check Python version
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Found Python %PYVER%

:: Get the directory where this script lives
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

echo.
echo [1/4] Installing core dependencies...
python -m pip install --upgrade pip
python -m pip install -e "."

echo.
echo [2/4] Installing PyTorch with CUDA support...
echo   (This is ~2.5GB download, may take a few minutes)
python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

echo.
echo [3/5] Installing transcription and diarization...
python -m pip install faster-whisper pyannote.audio

echo.
echo [4/5] Installing integrations (Outlook + Google Drive)...
python -m pip install pywin32 google-api-python-client google-auth-oauthlib

echo.
echo [5/5] Creating shortcuts...

:: Desktop shortcut
set PYTHONW_PATH=
for /f "delims=" %%p in ('python -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set PYTHONW_PATH=%%p

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $sc = $ws.CreateShortcut('%USERPROFILE%\Desktop\Meeting Recorder.lnk'); ^
   $sc.TargetPath = '%PYTHONW_PATH%'; ^
   $sc.Arguments = '-m meeting_recorder'; ^
   $sc.WorkingDirectory = '%SCRIPT_DIR%'; ^
   $sc.IconLocation = '%SCRIPT_DIR%meeting_recorder.ico'; ^
   $sc.Description = 'Meeting Recorder - Record and transcribe meetings'; ^
   $sc.Save()"
echo   Created: Desktop shortcut

:: Start Menu shortcut
set STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $sc = $ws.CreateShortcut('%STARTMENU%\Meeting Recorder.lnk'); ^
   $sc.TargetPath = '%PYTHONW_PATH%'; ^
   $sc.Arguments = '-m meeting_recorder'; ^
   $sc.WorkingDirectory = '%SCRIPT_DIR%'; ^
   $sc.IconLocation = '%SCRIPT_DIR%meeting_recorder.ico'; ^
   $sc.Description = 'Meeting Recorder - Record and transcribe meetings'; ^
   $sc.Save()"
echo   Created: Start Menu shortcut (searchable in Windows)

:: Startup shortcut (auto-launch)
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $sc = $ws.CreateShortcut('%STARTUP%\Meeting Recorder.lnk'); ^
   $sc.TargetPath = '%PYTHONW_PATH%'; ^
   $sc.Arguments = '-m meeting_recorder'; ^
   $sc.WorkingDirectory = '%SCRIPT_DIR%'; ^
   $sc.IconLocation = '%SCRIPT_DIR%meeting_recorder.ico'; ^
   $sc.Description = 'Meeting Recorder - Auto-start'; ^
   $sc.Save()"
echo   Created: Startup shortcut (auto-launch on login)

echo.
echo ============================================================
echo   Setup complete!
echo ============================================================
echo.
echo   Desktop shortcut:    Meeting Recorder
echo   Windows Search:      Type "Meeting Recorder"
echo   Auto-start:          Launches on Windows login
echo.
echo   First-time setup:
echo     1. Launch Meeting Recorder from desktop
echo     2. Right-click tray icon ^> Settings
echo     3. Add your HuggingFace token for speaker diarization
echo        (get one at https://huggingface.co/settings/tokens)
echo     4. For Google Drive backup: place google_credentials.json in
echo        %%USERPROFILE%%\.meeting_recorder\ and enable in Settings
echo.
echo   Hotkeys:
echo     Ctrl+Shift+R  Start/stop recording
echo     Alt+A         Mute sync (Zoom)
echo     Ctrl+Shift+M  Mute sync (Teams)
echo     Ctrl+M        Mute sync (Webex)
echo     Ctrl+Shift+U  Manual mic mute toggle
echo.
echo   Integrations:
echo     Outlook:       Auto-detects meeting name from your calendar
echo     Google Drive:  Backs up recordings after transcription
echo.
pause
