@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.11 run.py
) else (
  python run.py
)
pause

