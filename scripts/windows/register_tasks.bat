@echo off
rem Registers the scheduled tasks. Run once from an admin Command Prompt. Edit times to taste.
rem Runs from the repo root with the project's virtual environment. Exit code is passed through,
rem so Task Scheduler shows a failed run as failed. Logs go to data\logs (JSON lines).
setlocal
cd /d "%~dp0..\.."
set ROOT=%CD%
schtasks /Create /F /TN "ReitStreet\sync" /TR "\"%ROOT%\scripts\windows\sync.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 13:00 /RI 30 /DU 07:00
schtasks /Create /F /TN "ReitStreet\ir-fetch-am" /TR "\"%ROOT%\scripts\windows\ir_fetch.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 06:00
schtasks /Create /F /TN "ReitStreet\ir-fetch-pm" /TR "\"%ROOT%\scripts\windows\ir_fetch.bat\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:00
schtasks /Create /F /TN "ReitStreet\weekly" /TR "\"%ROOT%\scripts\windows\weekly.bat\"" /SC WEEKLY /D SUN /ST 06:00
schtasks /Create /F /TN "ReitStreet\monitor" /TR "\"%ROOT%\scripts\windows\monitor.bat\"" /SC HOURLY
echo Registered. Set each task to "Run whether user is logged on or not" in Task Scheduler if the PC is shared.
