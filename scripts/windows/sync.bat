@echo off
rem Incremental SEC sync, then data quality checks. Schedule every 30 minutes on weekdays.
rem Runs from the repo root with the project's virtual environment. Exit code is passed through,
rem so Task Scheduler shows a failed run as failed. Logs go to data\logs (JSON lines).
setlocal
cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat || exit /b 2
reitstreet sec-sync
set RC=%ERRORLEVEL%
reitstreet dq
reitstreet monitor
exit /b %RC%
