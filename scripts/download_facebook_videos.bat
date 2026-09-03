@echo off
setlocal
cd /d "%~dp0\.."
python scripts\download_facebook_videos.py %*
exit /b %ERRORLEVEL%
