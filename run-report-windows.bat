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
echo OpenCode Deep Audit - read-only; no config will be changed.
echo Leave the path blank to audit global user settings only.
echo Enter a source-code project folder to include project-specific config and language detection.
set /p "TARGET_DIR=Project folder (blank = global settings only): "
echo.
echo Optional: check live MCP connections? This may start local processes or contact remote servers.
set /p "CHECK_MCP=Run that optional connection check? [y/N]: "
set "CHECK_ARGS="
if /I "%CHECK_MCP%"=="Y" set "CHECK_ARGS=--check-mcp"
if "%TARGET_DIR%"=="" (
  %PYTHON% "%SCRIPT_DIR%scripts\deep_audit.py" --global-only %CHECK_ARGS% --output "%SCRIPT_DIR%opencode-deep-audit.md"
) else (
  %PYTHON% "%SCRIPT_DIR%scripts\deep_audit.py" --project-dir "%TARGET_DIR%" %CHECK_ARGS% --output "%SCRIPT_DIR%opencode-deep-audit.md"
)
if errorlevel 1 (
  echo.
  echo Audit failed. Confirm Python 3.10+ is installed and try again.
  pause
  exit /b 1
)

if exist "%SCRIPT_DIR%opencode-deep-audit.md" (
  start "" notepad.exe "%SCRIPT_DIR%opencode-deep-audit.md"
)
echo.
echo The audit never applies recommendations. If you selected MCP, it may have checked connections.
pause
