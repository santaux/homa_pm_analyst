@echo off
REM Homa Energy PM Analytics — one-time setup (Windows)

cd /d "%~dp0"

REM 1. Extract the analytics database if still zipped
if not exist homa_pm_events.sqlite3 (
    if exist homa_pm_events.sqlite3.zip (
        echo Extracting homa_pm_events.sqlite3...
        powershell -Command "Expand-Archive -Force homa_pm_events.sqlite3.zip ."
    ) else (
        echo ERROR: neither homa_pm_events.sqlite3 nor homa_pm_events.sqlite3.zip found.
        exit /b 1
    )
)

REM 2. Create venv and install MCP server deps
if not exist .venv (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Python not found. Install Python 3.10+ from https://python.org
        exit /b 1
    )
)
echo Installing MCP server dependencies...
.venv\Scripts\pip install -r mcp\requirements.txt -q

echo.
echo Updating .mcp.json for Windows paths...
(
echo {
echo   "mcpServers": {
echo     "homa-pm-analytics": {
echo       "command": ".venv\\Scripts\\python.exe",
echo       "args": ["mcp/server.py"]
echo     }
echo   }
echo }
) > .mcp.json

REM 3. Start Metabase
where docker >nul 2>nul
if %errorlevel% equ 0 (
    echo Starting Metabase...
    pushd metabase
    docker compose up -d
    popd
    echo Metabase boot can take 60-90 s the first time.
) else (
    echo.
    echo WARNING: Docker not found. Install Docker Desktop to use dashboards.
    echo You can still use Claude Code + MCP for data analysis.
)

echo.
echo Setup complete.
echo.
echo Next steps:
echo   1. Wait for Metabase to be healthy (about a minute on first boot)
echo   2. Dashboards:  http://localhost:3000  ^>  admin@homa.local / HomaAdmin1!
echo   3. Claude Code: open this folder, then run  claude .
