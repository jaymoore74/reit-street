@echo off
rem IR website fetch: new supplementals from each REIT's results page, then the freshness and
rem site-health checks and the monitor (layout breaks, failing pages, overdue supplementals).
rem Schedule twice on weekdays (register_tasks.bat: 06:00 and 15:00).
rem Runs from the repo root with the project's virtual environment. Exit code is passed through,
rem so Task Scheduler shows a failed run as failed. Logs go to data\logs (JSON lines).
setlocal
cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat || exit /b 2
reitstreet fetch-ir
set RC=%ERRORLEVEL%
reitstreet dq --only freshness,ops
reitstreet monitor
exit /b %RC%
