#!/usr/bin/env python3
"""
One-shot setup for BTC/ETH Bitunix Futures Analyzer.
Run this once after cloning the repo:

    python setup.py

It will:
  1. Install required Python packages
  2. Create your .env file with API credentials
  3. Test the Bitunix API connection
  4. Create a desktop shortcut for your OS
"""

import os
import sys
import platform
import subprocess
import shutil
from pathlib import Path

HERE     = Path(__file__).parent.resolve()
ENV_FILE = HERE / ".env"

# ── Colours (no colorama yet at this point) ───────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}✔{RESET}  {msg}")
def err(msg):  print(f"  {RED}✖{RESET}  {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET}  {msg}")
def info(msg): print(f"  {CYAN}→{RESET}  {msg}")
def header(msg):
    print(f"\n{BOLD}{CYAN}{'─' * 55}{RESET}")
    print(f"  {BOLD}{msg}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 55}{RESET}")


# ── Step 1: Install dependencies ─────────────────────────────────────────────

def install_deps():
    header("Step 1 — Installing dependencies")
    req = HERE / "requirements.txt"
    if not req.exists():
        err("requirements.txt not found — are you in the right folder?")
        sys.exit(1)

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        ok("All packages installed (requests, pandas, numpy, colorama)")
    else:
        err("pip install failed:")
        print(result.stderr)
        sys.exit(1)


# ── Step 2: Configure .env ────────────────────────────────────────────────────

def configure_env():
    header("Step 2 — API credentials")

    existing_key    = ""
    existing_secret = ""

    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("BITUNIX_API_KEY="):
                existing_key = line.split("=", 1)[1].strip()
            elif line.startswith("BITUNIX_API_SECRET="):
                existing_secret = line.split("=", 1)[1].strip()

    if existing_key and existing_secret:
        info(f"Existing credentials found (key ends …{existing_key[-6:]})")
        keep = input("  Keep existing credentials? [Y/n]: ").strip().lower()
        if keep in ("", "y", "yes"):
            ok("Keeping existing credentials")
            return
        print()

    print("  Enter your Bitunix API credentials.")
    print("  (Get them at: Bitunix → Account → API Management)\n")

    api_key = input("  API Key    : ").strip()
    api_sec = input("  API Secret : ").strip()

    if not api_key or not api_sec:
        warn("No credentials entered — skipping .env creation.")
        warn("You can add them later by editing .env manually.")
        return

    ENV_FILE.write_text(
        f"BITUNIX_API_KEY={api_key}\n"
        f"BITUNIX_API_SECRET={api_sec}\n"
    )
    ok(".env file created (gitignored — safe from commits)")


# ── Step 3: Test API connection ───────────────────────────────────────────────

def test_connection():
    header("Step 3 — Testing Bitunix API connection")

    # Load .env
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    try:
        import requests

        # Public market data test (no auth needed)
        info("Testing public market data endpoint…")
        r = requests.get(
            "https://fapi.bitunix.com/api/v1/futures/market/kline",
            params={"symbol": "BTCUSDT", "interval": "60", "limit": "1"},
            timeout=8
        )
        r.raise_for_status()
        body = r.json()
        if body.get("code") == 0 and body.get("data"):
            price = float(body["data"][-1]["close"])
            ok(f"Market data OK  —  BTC price: ${price:,.2f}")
        else:
            warn(f"Unexpected response: {body}")

        # Authenticated account test
        api_key = os.getenv("BITUNIX_API_KEY", "")
        api_sec = os.getenv("BITUNIX_API_SECRET", "")
        if api_key and api_sec:
            info("Testing authenticated account endpoint…")
            import hmac, hashlib, time
            nonce = str(int(time.time() * 1000))
            msg   = api_key + nonce
            sig   = hmac.new(api_sec.encode(), msg.encode(), hashlib.sha256).hexdigest()
            headers = {
                "api-key":   api_key,
                "sign":      sig,
                "nonce":     nonce,
                "timestamp": nonce,
                "Content-Type": "application/json",
            }
            ar = requests.get(
                "https://fapi.bitunix.com/api/v1/futures/account",
                headers=headers, timeout=8
            )
            ab = ar.json()
            if ab.get("code") == 0:
                data      = ab["data"]
                balance   = float(data.get("marginBalance") or data.get("balance") or 0)
                available = float(data.get("available") or data.get("availableBalance") or 0)
                ok(f"Account auth OK  —  balance: ${balance:,.2f}  available: ${available:,.2f}")
            else:
                warn(f"Auth response (code {ab.get('code')}): {ab.get('msg')}")
                warn("Credentials may be wrong, or IP restriction is enabled on this key.")
        else:
            warn("No API credentials — skipping account test.")

    except requests.ConnectionError:
        err("Cannot reach fapi.bitunix.com — check your internet connection.")
    except Exception as e:
        err(f"Connection test failed: {e}")


# ── Step 4: Desktop shortcut ──────────────────────────────────────────────────

def create_shortcut():
    header("Step 4 — Desktop shortcut")

    system  = platform.system()
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.home() / "desktop"   # some Linux setups
    if not desktop.exists():
        warn("Desktop folder not found — shortcut skipped.")
        info("You can run the tool directly:  python crypto_analyzer.py")
        return

    if system == "Windows":
        _shortcut_windows(desktop)
    elif system == "Darwin":
        _shortcut_mac(desktop)
    else:
        _shortcut_linux(desktop)


def _shortcut_windows(desktop: Path):
    target  = desktop / "CryptoAnalyzer.bat"
    bat_src = HERE / "launch.bat"
    if bat_src.exists():
        shutil.copy(bat_src, target)
        ok(f"Shortcut created: {target}")
        info("Double-click it to launch the analyzer.")
    else:
        # Write inline
        target.write_text(
            f'@echo off\ncd /d "{HERE}"\npython crypto_analyzer.py %*\npause\n'
        )
        ok(f"Shortcut created: {target}")


def _shortcut_mac(desktop: Path):
    app_path = desktop / "CryptoAnalyzer.app"
    contents = app_path / "Contents" / "MacOS"
    contents.mkdir(parents=True, exist_ok=True)

    script = contents / "CryptoAnalyzer"
    script.write_text(
        f'#!/bin/bash\n'
        f'cd "{HERE}"\n'
        f'osascript -e \'tell app "Terminal" to do script '
        f'"cd \\"{HERE}\\" && python3 crypto_analyzer.py"\'\n'
    )
    script.chmod(0o755)

    # Basic Info.plist
    (app_path / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        '  <key>CFBundleName</key><string>CryptoAnalyzer</string>\n'
        '  <key>CFBundleExecutable</key><string>CryptoAnalyzer</string>\n'
        '  <key>CFBundleIdentifier</key><string>com.cryptoanalyzer</string>\n'
        '  <key>CFBundleVersion</key><string>1.0</string>\n'
        '</dict></plist>\n'
    )
    ok(f"App created: {app_path}")
    info("Double-click CryptoAnalyzer.app on your Desktop to launch.")


def _shortcut_linux(desktop: Path):
    src = HERE / "CryptoAnalyzer.desktop"
    dst = desktop / "CryptoAnalyzer.desktop"

    content = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=BTC/ETH Analyzer\n"
        "Comment=Bitunix Futures signal analyzer\n"
        f"Exec=bash -c 'cd \"{HERE}\" && bash launch.sh; exec bash'\n"
        "Icon=utilities-terminal\n"
        "Terminal=true\n"
        "Categories=Finance;\n"
    )
    dst.write_text(content)
    dst.chmod(0o755)
    ok(f"Shortcut created: {dst}")
    info("Right-click it → 'Allow Launching' if needed.")


# ── Step 5: Quick demo run ────────────────────────────────────────────────────

def demo_run():
    header("Step 5 — Quick demo run")
    info("Running analyzer with synthetic data to confirm everything works…\n")
    result = subprocess.run(
        [sys.executable, str(HERE / "crypto_analyzer.py"), "--demo", "--coins", "BTC"],
        cwd=str(HERE)
    )
    if result.returncode == 0:
        ok("Demo run successful!")
    else:
        err("Demo run failed — check errors above.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════════════════╗")
    print(f"║   BTC/ETH Bitunix Futures Analyzer — Setup          ║")
    print(f"╚══════════════════════════════════════════════════════╝{RESET}\n")

    install_deps()
    configure_env()
    test_connection()
    create_shortcut()
    demo_run()

    print(f"\n{BOLD}{GREEN}✔ Setup complete!{RESET}\n")
    print("  Run the tool anytime with:")
    print(f"    {CYAN}python crypto_analyzer.py{RESET}              # live signals")
    print(f"    {CYAN}python crypto_analyzer.py --account{RESET}    # account snapshot")
    print(f"    {CYAN}python crypto_analyzer.py --monitor{RESET}    # continuous alerts")
    print(f"    {CYAN}python crypto_analyzer.py --demo{RESET}       # offline test\n")


if __name__ == "__main__":
    main()
