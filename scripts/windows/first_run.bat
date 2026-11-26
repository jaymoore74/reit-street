@echo off
rem One-time setup after config.ini exists: schema, universe, full SEC history, spot check.
rem Runs from the repo root with the project's virtual environment. Exit code is passed through,
rem so Task Scheduler shows a failed run as failed. Logs go to data\logs (JSON lines).
setlocal
cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat || exit /b 2
reitstreet config || exit /b %ERRORLEVEL%
reitstreet migrate || exit /b %ERRORLEVEL%
reitstreet universe || exit /b %ERRORLEVEL%
reitstreet sec-sync --full-history
set RC=%ERRORLEVEL%
reitstreet dq
reitstreet spotcheck --tickers PLD,O,SPG
reitstreet status
exit /b %RC%
