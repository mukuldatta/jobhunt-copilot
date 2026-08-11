@echo off
REM Starts the JobHunt Copilot backend locally.
REM Scraping Naukri/Indeed and auto-apply drive a visible Chrome window, so the
REM backend must run on your own machine (not a headless server).

where py >nul 2>nul
if %ERRORLEVEL%==0 (set PYTHON=py) else (set PYTHON=python)

echo Installing dependencies...
%PYTHON% -m pip install -r requirements.txt --quiet
%PYTHON% -m playwright install chromium

echo.
echo Starting backend on http://localhost:8000 ...
cd backend
%PYTHON% -m uvicorn main:app --reload --port 8000
