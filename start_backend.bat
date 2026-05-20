@echo off
set PYTHON=C:\Users\mukul\AppData\Local\Python\bin\python.exe

echo Installing dependencies with real Python...
"%PYTHON%" -m pip install -r requirements.txt --quiet

echo.
echo Starting JobHunt Copilot backend...
cd backend
"%PYTHON%" -m uvicorn main:app --reload --port 8000
