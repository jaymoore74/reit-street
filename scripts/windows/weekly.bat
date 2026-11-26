@echo off
rem Weekly full refresh: universe, forced XBRL reload, IR fetch, parse, DQ, exports, data dictionary.
rem Runs from the repo root with the project's virtual environment. Exit code is passed through,
rem so Task Scheduler shows a failed run as failed. Logs go to data\logs (JSON lines).
setlocal
cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat || exit /b 2
reitstreet universe || exit /b %ERRORLEVEL%
reitstreet sec-sync --force
set RC=%ERRORLEVEL%
reitstreet fetch-ir
reitstreet parse-docs
reitstreet dq
reitstreet export
reitstreet monitor
exit /b %RC%
