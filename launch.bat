@echo off
title BTC/ETH Bitunix Futures Analyzer
color 0A
cls

echo.
echo  ==========================================
echo   BTC / ETH  Bitunix Futures Analyzer
echo  ==========================================
echo.

cd /d "%~dp0"

REM ── Check Python ──────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Download from https://python.org
    pause
    exit /b 1
)

REM ── Install dependencies if missing ───────────────────────
python -c "import requests, pandas, numpy, colorama" >nul 2>&1
if errorlevel 1 (
    echo  Installing dependencies...
    pip install -q -r requirements.txt
    echo.
)

REM ── Run analyzer ──────────────────────────────────────────
python crypto_analyzer.py %*

echo.
pause
