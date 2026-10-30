@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000"
uvicorn app:app
pause
