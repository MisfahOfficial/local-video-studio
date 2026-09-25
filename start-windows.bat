@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.11 -c "import certifi, yt_dlp" >nul 2>nul || py -3.11 -m pip install --disable-pip-version-check -q certifi yt-dlp
  py -3.11 run.py
) else (
  python -c "import certifi, yt_dlp" >nul 2>nul || python -m pip install --disable-pip-version-check -q certifi yt-dlp
  python run.py
)
pause
