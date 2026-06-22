#!/usr/bin/env python3
"""
BTC/ETH Trading Signal Analyzer
Fetches live OHLCV data from Binance and generates LONG/SHORT signals with TP and SL.

Usage:
  python crypto_analyzer.py                        # analyze BTC and ETH on 1h candles
  python crypto_analyzer.py --coins BTC --interval 4h
  python crypto_analyzer.py --monitor --refresh 300
  python crypto_analyzer.py --demo                 # offline demo with synthetic data
"""

import argparse
import time
import sys
from datetime import datetime

import requests
import pandas as pd
import numpy as np
from colorama import Fore, Style, init

init(autoreset=True)

BINANCE_BASE = "https://api.binance.com/api/v3"
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

# ── Data Fetching ────────────────────────────────────────────────────────────

def fetch_ohlcv(symbol: str, interval: str = "1h", limit: int = 200) -> pd.DataFrame:
    url = f"{BINANCE_BASE}/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def fetch_price(symbol: str) -> float:
    url = f"{BINANCE_BASE}/ticker/price"
    resp = requests.get(url, params={"symbol": symbol}, timeout=5)
    resp.raise_for_status()
    return float(resp.json()["price"])


def generate_demo_ohlcv(seed_price: float, limit: int = 200) -> pd.DataFrame:
    """Generate realistic synthetic OHLCV data for offline testing."""
    np.random.seed(42)
    returns = np.random.normal(0.0002, 0.018, limit)
    prices = seed_price * np.cumprod(1 + returns)

    rows = []
    for i, close in enumerate(prices):
        spread = close * 0.008
        high = close + abs(np.random.normal(0, spread))
        low = close - abs(np.random.normal(0, spread))
        open_ = prices[i - 1] if i > 0 else close
        vol = np.random.uniform(100, 5000)
        ts = pd.Timestamp("2025-01-01") + pd.Timedelta(hours=i)
        rows.append({"open_time": ts, "open": open_, "high": high, "low": low, "close": close, "volume": vol})

    return pd.DataFrame(rows)


# ── Technical Indicators ─────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def stochastic_rsi(series: pd.Series, rsi_period: int = 14, stoch_period: int = 14) -> pd.Series:
    rsi_vals = rsi(series, rsi_period)
    min_rsi = rsi_vals.rolling(stoch_period).min()
    max_rsi = rsi_vals.rolling(stoch_period).max()
    stoch = (rsi_vals - min_rsi) / (max_rsi - min_rsi).replace(0, np.nan) * 100
    return stoch


# ── Signal Engine ─────────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]

    rsi_val = rsi(close).iloc[-1]
    stoch_rsi_val = stochastic_rsi(close).iloc[-1]

    macd_line, sig_line, histogram = macd(close)
    macd_hist = histogram.iloc[-1]
    macd_prev_hist = histogram.iloc[-2]

    bb_upper, bb_mid, bb_lower = bollinger_bands(close)
    bb_upper_val = bb_upper.iloc[-1]
    bb_lower_val = bb_lower.iloc[-1]
    bb_mid_val = bb_mid.iloc[-1]
    bb_width_pct = (bb_upper_val - bb_lower_val) / bb_mid_val * 100

    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    ema200 = ema(close, 200).iloc[-1]

    atr_val = atr(df).iloc[-1]
    price = close.iloc[-1]

    # Volume trend (last 5 vs prior 15 candles)
    vol_short = df["volume"].iloc[-5:].mean()
    vol_long = df["volume"].iloc[-20:-5].mean()
    vol_ratio = vol_short / vol_long if vol_long > 0 else 1.0

    # Price momentum (rate of change)
    roc_10 = (price - close.iloc[-10]) / close.iloc[-10] * 100

    return {
        "price": price,
        "rsi": rsi_val,
        "stoch_rsi": stoch_rsi_val,
        "macd_hist": macd_hist,
        "macd_prev_hist": macd_prev_hist,
        "bb_upper": bb_upper_val,
        "bb_mid": bb_mid_val,
        "bb_lower": bb_lower_val,
        "bb_width_pct": bb_width_pct,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "atr": atr_val,
        "vol_ratio": vol_ratio,
        "roc_10": roc_10,
    }


def generate_signal(ind: dict) -> dict:
    """
    Multi-factor scoring: each indicator votes bullish (+) or bearish (-).
    Score >= 4  → LONG
    Score <= -4 → SHORT
    Otherwise   → NEUTRAL

    TP/SL are ATR-based with 2:1 R/R minimum.
    """
    score = 0
    reasons = []
    price = ind["price"]

    # ── RSI ──────────────────────────────────────────────
    if ind["rsi"] < 30:
        score += 2
        reasons.append(f"RSI strongly oversold ({ind['rsi']:.1f})")
    elif ind["rsi"] < 45:
        score += 1
        reasons.append(f"RSI bullish zone ({ind['rsi']:.1f})")
    elif ind["rsi"] > 70:
        score -= 2
        reasons.append(f"RSI strongly overbought ({ind['rsi']:.1f})")
    elif ind["rsi"] > 55:
        score -= 1
        reasons.append(f"RSI bearish zone ({ind['rsi']:.1f})")
    else:
        reasons.append(f"RSI neutral ({ind['rsi']:.1f})")

    # ── Stochastic RSI ───────────────────────────────────
    if not np.isnan(ind["stoch_rsi"]):
        if ind["stoch_rsi"] < 20:
            score += 1
            reasons.append(f"Stoch RSI oversold ({ind['stoch_rsi']:.1f})")
        elif ind["stoch_rsi"] > 80:
            score -= 1
            reasons.append(f"Stoch RSI overbought ({ind['stoch_rsi']:.1f})")

    # ── MACD ─────────────────────────────────────────────
    if ind["macd_hist"] > 0 and ind["macd_prev_hist"] <= 0:
        score += 2
        reasons.append("MACD bullish crossover ↑")
    elif ind["macd_hist"] < 0 and ind["macd_prev_hist"] >= 0:
        score -= 2
        reasons.append("MACD bearish crossover ↓")
    elif ind["macd_hist"] > 0:
        score += 1
        reasons.append(f"MACD histogram positive ({ind['macd_hist']:.4f})")
    elif ind["macd_hist"] < 0:
        score -= 1
        reasons.append(f"MACD histogram negative ({ind['macd_hist']:.4f})")

    # ── Bollinger Bands ───────────────────────────────────
    if price < ind["bb_lower"]:
        score += 1
        reasons.append("Price below lower BB (mean-reversion buy)")
    elif price > ind["bb_upper"]:
        score -= 1
        reasons.append("Price above upper BB (mean-reversion sell)")

    # ── EMA Trend Alignment ──────────────────────────────
    if ind["ema20"] > ind["ema50"] > ind["ema200"]:
        score += 2
        reasons.append("Strong bull trend: EMA 20>50>200")
    elif ind["ema20"] < ind["ema50"] < ind["ema200"]:
        score -= 2
        reasons.append("Strong bear trend: EMA 20<50<200")
    elif ind["ema20"] > ind["ema50"]:
        score += 1
        reasons.append("Short-term uptrend: EMA 20>50")
    elif ind["ema20"] < ind["ema50"]:
        score -= 1
        reasons.append("Short-term downtrend: EMA 20<50")

    # ── Macro Trend (price vs EMA200) ────────────────────
    if price > ind["ema200"]:
        score += 1
        reasons.append("Above EMA200 — bullish macro")
    else:
        score -= 1
        reasons.append("Below EMA200 — bearish macro")

    # ── Volume Confirmation ──────────────────────────────
    if ind["vol_ratio"] > 1.5:
        if score > 0:
            score += 1
            reasons.append(f"Volume surge confirms bullish move ({ind['vol_ratio']:.1f}x avg)")
        elif score < 0:
            score -= 1
            reasons.append(f"Volume surge confirms bearish move ({ind['vol_ratio']:.1f}x avg)")

    # ── Momentum (10-period ROC) ─────────────────────────
    if ind["roc_10"] > 3:
        score += 1
        reasons.append(f"Strong upward momentum ROC={ind['roc_10']:.2f}%")
    elif ind["roc_10"] < -3:
        score -= 1
        reasons.append(f"Strong downward momentum ROC={ind['roc_10']:.2f}%")

    # ── Direction Decision ────────────────────────────────
    if score >= 4:
        direction = "LONG"
    elif score <= -4:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # ── ATR-based TP / SL (2:1 reward:risk) ──────────────
    atr_val = ind["atr"]
    sl_mult = 1.5
    tp_mult = 3.0

    if direction == "LONG":
        entry = price
        sl = round(entry - sl_mult * atr_val, 2)
        tp = round(entry + tp_mult * atr_val, 2)
        sl_pct = round((entry - sl) / entry * 100, 2)
        tp_pct = round((tp - entry) / entry * 100, 2)
    elif direction == "SHORT":
        entry = price
        sl = round(entry + sl_mult * atr_val, 2)
        tp = round(entry - tp_mult * atr_val, 2)
        sl_pct = round((sl - entry) / entry * 100, 2)
        tp_pct = round((entry - tp) / entry * 100, 2)
    else:
        entry, sl, tp, sl_pct, tp_pct = price, None, None, None, None

    return {
        "direction": direction,
        "score": score,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "reasons": reasons,
    }


# ── Display ───────────────────────────────────────────────────────────────────

def direction_color(direction: str) -> str:
    return {
        "LONG": Fore.GREEN,
        "SHORT": Fore.RED,
        "NEUTRAL": Fore.YELLOW,
    }.get(direction, Fore.WHITE)


def strength_bar(score: int) -> str:
    MAX = 10
    filled = min(abs(score), MAX)
    bar = "█" * filled + "░" * (MAX - filled)
    color = Fore.GREEN if score > 0 else Fore.RED if score < 0 else Fore.YELLOW
    return f"{color}[{bar}]{Style.RESET_ALL} {score:+d}"


def print_analysis(coin: str, ind: dict, sig: dict, interval: str):
    d = direction_color(sig["direction"])
    price_fmt = f"${ind['price']:,.2f}"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n╔{'═' * 58}╗")
    print(f"║  {Fore.CYAN}{Style.BRIGHT}{coin}/USDT{Style.RESET_ALL}  │  {price_fmt}  │  {interval}  │  {now}  ║")
    print(f"╠{'═' * 58}╣")

    signal_line = f"  Signal:  {d}{Style.BRIGHT}{sig['direction']:7s}{Style.RESET_ALL}  {strength_bar(sig['score'])}"
    print(f"║{signal_line}")
    print(f"╠{'─' * 58}╣")

    if sig["direction"] != "NEUTRAL":
        print(f"║  Entry:   {Style.BRIGHT}${sig['entry']:>12,.2f}{Style.RESET_ALL}")
        print(f"║  {Fore.GREEN}TP:     ${sig['tp']:>12,.2f}  (+{sig['tp_pct']:.2f}%){Style.RESET_ALL}")
        print(f"║  {Fore.RED}SL:     ${sig['sl']:>12,.2f}  (-{sig['sl_pct']:.2f}%){Style.RESET_ALL}")
        rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] else 0
        print(f"║  R/R:     1 : {rr:.1f}")
    else:
        print(f"║  No trade — wait for a clearer setup.")

    print(f"╠{'─' * 58}╣")
    print(f"║  {Style.BRIGHT}Indicators:{Style.RESET_ALL}")
    print(f"║    RSI(14)      : {ind['rsi']:.1f}")
    print(f"║    Stoch RSI    : {ind['stoch_rsi']:.1f}")
    print(f"║    MACD hist    : {ind['macd_hist']:.5f}")
    print(f"║    BB upper/lower: ${ind['bb_upper']:,.2f} / ${ind['bb_lower']:,.2f}  (width {ind['bb_width_pct']:.1f}%)")
    print(f"║    EMA 20       : ${ind['ema20']:,.2f}")
    print(f"║    EMA 50       : ${ind['ema50']:,.2f}")
    print(f"║    EMA 200      : ${ind['ema200']:,.2f}")
    print(f"║    ATR(14)      : ${ind['atr']:,.2f}")
    print(f"║    Volume ratio : {ind['vol_ratio']:.2f}x  (vs 15-candle avg)")
    print(f"║    ROC(10)      : {ind['roc_10']:+.2f}%")

    print(f"╠{'─' * 58}╣")
    print(f"║  {Style.BRIGHT}Signal reasons:{Style.RESET_ALL}")
    for r in sig["reasons"]:
        print(f"║    • {r}")
    print(f"╚{'═' * 58}╝\n")


def print_alert(coin: str, sig: dict, prev_direction: str):
    d = direction_color(sig["direction"])
    print(f"\n{'▶' * 3} {Fore.YELLOW}{Style.BRIGHT}SIGNAL CHANGE ALERT{Style.RESET_ALL} {'◀' * 3}")
    print(f"  {Fore.CYAN}{coin}/USDT{Style.RESET_ALL}  {prev_direction} → {d}{Style.BRIGHT}{sig['direction']}{Style.RESET_ALL}")
    print(f"  Entry: ${sig['entry']:,.2f}")
    if sig["tp"]:
        print(f"  {Fore.GREEN}TP: ${sig['tp']:,.2f}  (+{sig['tp_pct']:.2f}%){Style.RESET_ALL}")
        print(f"  {Fore.RED}SL: ${sig['sl']:,.2f}  (-{sig['sl_pct']:.2f}%){Style.RESET_ALL}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'▶' * 40}\n")


# ── Analysis Runner ───────────────────────────────────────────────────────────

def analyze(coins: list, interval: str, verbose: bool, demo: bool = False) -> dict:
    demo_prices = {"BTC": 107000.0, "ETH": 2520.0}
    results = {}

    for coin in coins:
        symbol = SYMBOLS[coin]
        try:
            if demo:
                df = generate_demo_ohlcv(demo_prices[coin])
                print(f"{Fore.YELLOW}[DEMO]{Style.RESET_ALL} Using synthetic data for {coin}")
            else:
                df = fetch_ohlcv(symbol, interval=interval)

            ind = compute_indicators(df)
            sig = generate_signal(ind)
            results[coin] = (ind, sig)

            if verbose:
                print_analysis(coin, ind, sig, interval)

        except requests.HTTPError as e:
            print(f"{Fore.RED}HTTP error fetching {coin}: {e}{Style.RESET_ALL}")
        except requests.ConnectionError:
            print(f"{Fore.RED}Network error: cannot reach Binance. Check your connection or try --demo.{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}Error analyzing {coin}: {e}{Style.RESET_ALL}")

    return results


def monitor_loop(coins: list, interval: str, refresh: int, demo: bool):
    print(f"{Fore.CYAN}Monitoring {', '.join(coins)} every {refresh}s on {interval} candles.{Style.RESET_ALL}")
    print(f"Press Ctrl+C to stop.\n")
    prev_signals: dict[str, str] = {}

    while True:
        results = analyze(coins, interval, verbose=True, demo=demo)
        for coin, (ind, sig) in results.items():
            prev = prev_signals.get(coin, "NEUTRAL")
            if sig["direction"] != "NEUTRAL" and sig["direction"] != prev:
                print_alert(coin, sig, prev)
            prev_signals[coin] = sig["direction"]

        print(f"{Fore.WHITE}Next refresh in {refresh}s  [{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL}")
        try:
            time.sleep(refresh)
        except KeyboardInterrupt:
            print("\nStopped.")
            sys.exit(0)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BTC/ETH trading signal analyzer — LONG/SHORT with TP and SL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python crypto_analyzer.py                         # BTC + ETH, 1h, one-shot
  python crypto_analyzer.py --coins BTC --interval 4h
  python crypto_analyzer.py --monitor --refresh 300 # alert on signal changes
  python crypto_analyzer.py --demo                  # offline synthetic data
        """
    )
    parser.add_argument(
        "--coins", nargs="+", choices=["BTC", "ETH"], default=["BTC", "ETH"],
        metavar="COIN", help="Coins to analyze: BTC ETH (default: both)"
    )
    parser.add_argument(
        "--interval", default="1h",
        choices=["5m", "15m", "30m", "1h", "4h", "1d"],
        help="Candle interval (default: 1h)"
    )
    parser.add_argument(
        "--monitor", action="store_true",
        help="Continuously monitor and alert on signal changes"
    )
    parser.add_argument(
        "--refresh", type=int, default=300,
        help="Seconds between refreshes in monitor mode (default: 300)"
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Use synthetic offline data (no internet required)"
    )
    args = parser.parse_args()

    if args.monitor:
        monitor_loop(args.coins, args.interval, args.refresh, args.demo)
    else:
        analyze(args.coins, args.interval, verbose=True, demo=args.demo)


if __name__ == "__main__":
    main()
