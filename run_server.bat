@echo off
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python server.py
    goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
    py server.py
    goto :eof
)

echo Python was not found on this PC.
echo Please install Python 3 from https://www.python.org/downloads/ and run this again.
pause
