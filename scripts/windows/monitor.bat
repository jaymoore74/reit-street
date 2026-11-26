@echo off
rem Health check and alerts. Schedule hourly.
rem Runs from the repo root with the project's virtual environment. Exit code is passed through,
rem so Task Scheduler shows a failed run as failed. Logs go to data\logs (JSON lines).
setlocal
cd /d "%~dp0..\.."
call .venv\Scripts\activate.bat || exit /b 2
reitstreet monitor
exit /b %ERRORLEVEL%
