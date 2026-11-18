@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

where py >nul 2>&1
if not errorlevel 1 (
  set "PYTHON=py -3"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo Python 3 is required to run this report.
    echo Download the official Windows installer from https://www.python.org/downloads/windows/
    echo During installation, enable the Python Launcher or add Python to PATH.
    pause
    exit /b 1
  )
  set "PYTHON=python"
)

echo.
echo OpenCode Feature Report - no configuration will be changed.
echo Paste the folder containing the OpenCode project you want to inspect.
set /p "TARGET_DIR=Project folder (leave blank to inspect your user-level config only): "
if "%TARGET_DIR%"=="" set "TARGET_DIR=%USERPROFILE%"

%PYTHON% "%SCRIPT_DIR%scripts\recommend_features.py" --project-dir "%TARGET_DIR%" --output "%SCRIPT_DIR%opencode-feature-report.md"
if errorlevel 1 (
  echo.
  echo Report generation failed. Confirm Python 3.10+ is installed and try again.
  pause
  exit /b 1
)

if exist "%SCRIPT_DIR%opencode-feature-report.md" (
  start "" notepad.exe "%SCRIPT_DIR%opencode-feature-report.md"
)
echo.
echo The report only recommends changes; it does not apply them.
pause
