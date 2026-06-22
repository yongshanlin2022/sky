@echo off
REM Launcher for BTC/ETH Bitunix Futures Analyzer (Windows)
REM Double-click this file or right-click → Send to Desktop (create shortcut)

cd /d "%~dp0"

REM Install dependencies if needed
python -c "import requests, pandas, numpy, colorama" 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
)

echo Starting BTC/ETH Analyzer...
python crypto_analyzer.py %*

pause
