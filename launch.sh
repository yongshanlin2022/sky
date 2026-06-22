#!/usr/bin/env bash
# Launcher for BTC/ETH Bitunix Futures Analyzer
# Place this file next to crypto_analyzer.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Install dependencies if needed
if ! python3 -c "import requests, pandas, numpy, colorama" 2>/dev/null; then
    echo "Installing dependencies..."
    pip3 install -r requirements.txt
fi

echo "Starting BTC/ETH Analyzer..."
python3 crypto_analyzer.py "$@"

read -rp "Press Enter to close..."
