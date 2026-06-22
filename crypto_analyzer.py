#!/usr/bin/env python3
"""
BTC/ETH Trading Signal Analyzer — Bitunix Futures
Fetches live OHLCV data from Bitunix perpetual futures and generates
LONG/SHORT signals with ATR-based TP/SL, multi-timeframe confirmation,
and position size calculation.

Usage:
  python crypto_analyzer.py                             # BTC + ETH, MTF analysis
  python crypto_analyzer.py --coins BTC --interval 1h  # single timeframe
  python crypto_analyzer.py --balance 10000 --risk 1   # position sizing
  python crypto_analyzer.py --monitor --refresh 300    # continuous alerts
  python crypto_analyzer.py --demo                     # offline synthetic data

API credentials — place in .env file or export:
  BITUNIX_API_KEY=your_key
  BITUNIX_API_SECRET=your_secret
"""

import argparse
import hashlib
import hmac
import os
import time
import sys
from datetime import datetime

import requests
import pandas as pd
import numpy as np
from colorama import Fore, Style, init

init(autoreset=True)

# ── Load .env ─────────────────────────────────────────────────────────────────

_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ── Bitunix Futures API ───────────────────────────────────────────────────────

BITUNIX_BASE = "https://fapi.bitunix.com"
SYMBOLS      = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

INTERVAL_MAP = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "4h": "240", "1d": "D",
}

# Timeframes used for multi-timeframe analysis, ordered low→high
MTF_TIMEFRAMES = ["1h", "4h", "1d"]


def _sign(secret: str, params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _auth_headers(api_key: str, api_secret: str, params: dict) -> dict:
    ts = str(int(time.time() * 1000))
    params["timestamp"] = ts
    params["api_key"] = api_key
    return {
        "api-key": api_key,
        "sign": _sign(api_secret, params),
        "timestamp": ts,
        "Content-Type": "application/json",
    }


def fetch_ohlcv(symbol: str, interval: str = "1h", limit: int = 200) -> pd.DataFrame:
    url = f"{BITUNIX_BASE}/api/v1/futures/market/kline"
    params = {"symbol": symbol, "interval": INTERVAL_MAP.get(interval, "60"), "limit": limit}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise ValueError(f"Bitunix API error: {body.get('msg', body)}")
    rows = [
        {
            "open_time": pd.to_datetime(int(c["time"]), unit="ms"),
            "open":  float(c["open"]),
            "high":  float(c["high"]),
            "low":   float(c["low"]),
            "close": float(c["close"]),
            "volume":float(c["volume"]),
        }
        for c in body["data"]
    ]
    return pd.DataFrame(rows).sort_values("open_time").reset_index(drop=True)


def generate_demo_ohlcv(seed_price: float, limit: int = 200, seed: int = 42) -> pd.DataFrame:
    np.random.seed(seed)
    returns = np.random.normal(0.0002, 0.018, limit)
    prices  = seed_price * np.cumprod(1 + returns)
    rows = []
    for i, close in enumerate(prices):
        spread = close * 0.008
        rows.append({
            "open_time": pd.Timestamp("2025-01-01") + pd.Timedelta(hours=i),
            "open":  prices[i - 1] if i > 0 else close,
            "high":  close + abs(np.random.normal(0, spread)),
            "low":   close - abs(np.random.normal(0, spread)),
            "close": close,
            "volume":np.random.uniform(100, 5000),
        })
    return pd.DataFrame(rows)


# ── Technical Indicators ──────────────────────────────────────────────────────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    ag    = gain.ewm(alpha=1 / period, adjust=False).mean()
    al    = loss.ewm(alpha=1 / period, adjust=False).mean()
    return 100 - (100 / (1 + ag / al.replace(0, np.nan)))

def macd(series: pd.Series, fast=12, slow=26, signal=9):
    ml  = ema(series, fast) - ema(series, slow)
    sl  = ema(ml, signal)
    return ml, sl, ml - sl

def bollinger_bands(series: pd.Series, period=20, std_dev=2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return mid + std_dev * std, mid, mid - std_dev * std

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()

def stochastic_rsi(series: pd.Series, rsi_p=14, stoch_p=14) -> pd.Series:
    r   = rsi(series, rsi_p)
    lo  = r.rolling(stoch_p).min()
    hi  = r.rolling(stoch_p).max()
    return (r - lo) / (hi - lo).replace(0, np.nan) * 100


# ── Signal Engine ─────────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]
    _, _, hist = macd(close)
    bb_u, bb_m, bb_l = bollinger_bands(close)
    price = close.iloc[-1]

    vol_short = df["volume"].iloc[-5:].mean()
    vol_long  = df["volume"].iloc[-20:-5].mean()

    return {
        "price":          price,
        "rsi":            rsi(close).iloc[-1],
        "stoch_rsi":      stochastic_rsi(close).iloc[-1],
        "macd_hist":      hist.iloc[-1],
        "macd_prev_hist": hist.iloc[-2],
        "bb_upper":       bb_u.iloc[-1],
        "bb_mid":         bb_m.iloc[-1],
        "bb_lower":       bb_l.iloc[-1],
        "bb_width_pct":   (bb_u.iloc[-1] - bb_l.iloc[-1]) / bb_m.iloc[-1] * 100,
        "ema20":          ema(close, 20).iloc[-1],
        "ema50":          ema(close, 50).iloc[-1],
        "ema200":         ema(close, 200).iloc[-1],
        "atr":            atr(df).iloc[-1],
        "vol_ratio":      vol_short / vol_long if vol_long > 0 else 1.0,
        "roc_10":         (price - close.iloc[-10]) / close.iloc[-10] * 100,
    }


def score_indicators(ind: dict) -> tuple[int, list[str]]:
    """Score a single timeframe's indicators. Returns (score, reasons)."""
    score, reasons, price = 0, [], ind["price"]

    if ind["rsi"] < 30:
        score += 2; reasons.append(f"RSI strongly oversold ({ind['rsi']:.1f})")
    elif ind["rsi"] < 45:
        score += 1; reasons.append(f"RSI bullish zone ({ind['rsi']:.1f})")
    elif ind["rsi"] > 70:
        score -= 2; reasons.append(f"RSI strongly overbought ({ind['rsi']:.1f})")
    elif ind["rsi"] > 55:
        score -= 1; reasons.append(f"RSI bearish zone ({ind['rsi']:.1f})")
    else:
        reasons.append(f"RSI neutral ({ind['rsi']:.1f})")

    if not np.isnan(ind["stoch_rsi"]):
        if ind["stoch_rsi"] < 20:
            score += 1; reasons.append(f"Stoch RSI oversold ({ind['stoch_rsi']:.1f})")
        elif ind["stoch_rsi"] > 80:
            score -= 1; reasons.append(f"Stoch RSI overbought ({ind['stoch_rsi']:.1f})")

    if ind["macd_hist"] > 0 and ind["macd_prev_hist"] <= 0:
        score += 2; reasons.append("MACD bullish crossover ↑")
    elif ind["macd_hist"] < 0 and ind["macd_prev_hist"] >= 0:
        score -= 2; reasons.append("MACD bearish crossover ↓")
    elif ind["macd_hist"] > 0:
        score += 1; reasons.append(f"MACD histogram positive ({ind['macd_hist']:.4f})")
    elif ind["macd_hist"] < 0:
        score -= 1; reasons.append(f"MACD histogram negative ({ind['macd_hist']:.4f})")

    if price < ind["bb_lower"]:
        score += 1; reasons.append("Price below lower BB (mean-reversion buy)")
    elif price > ind["bb_upper"]:
        score -= 1; reasons.append("Price above upper BB (mean-reversion sell)")

    if ind["ema20"] > ind["ema50"] > ind["ema200"]:
        score += 2; reasons.append("Strong bull trend: EMA 20>50>200")
    elif ind["ema20"] < ind["ema50"] < ind["ema200"]:
        score -= 2; reasons.append("Strong bear trend: EMA 20<50<200")
    elif ind["ema20"] > ind["ema50"]:
        score += 1; reasons.append("Short-term uptrend: EMA 20>50")
    elif ind["ema20"] < ind["ema50"]:
        score -= 1; reasons.append("Short-term downtrend: EMA 20<50")

    if price > ind["ema200"]:
        score += 1; reasons.append("Above EMA200 — bullish macro")
    else:
        score -= 1; reasons.append("Below EMA200 — bearish macro")

    if ind["vol_ratio"] > 1.5:
        if score > 0:
            score += 1; reasons.append(f"Volume surge confirms bull ({ind['vol_ratio']:.1f}x avg)")
        elif score < 0:
            score -= 1; reasons.append(f"Volume surge confirms bear ({ind['vol_ratio']:.1f}x avg)")

    if ind["roc_10"] > 3:
        score += 1; reasons.append(f"Strong upward momentum ROC={ind['roc_10']:.2f}%")
    elif ind["roc_10"] < -3:
        score -= 1; reasons.append(f"Strong downward momentum ROC={ind['roc_10']:.2f}%")

    return score, reasons


def generate_signal(ind: dict, mtf_bias: str = "NEUTRAL") -> dict:
    """
    Score indicators on the primary timeframe, then apply MTF bias bonus.

    MTF bias (from higher timeframes):
      BULL  → +2 bonus if primary leans long, blocks SHORT signals
      BEAR  → -2 bonus if primary leans short, blocks LONG signals
      NEUTRAL → no adjustment

    Thresholds: score >= 4 → LONG, <= -4 → SHORT.
    TP = entry ± 3×ATR,  SL = entry ± 1.5×ATR  (2:1 R/R)
    """
    score, reasons = score_indicators(ind)

    # Apply MTF bias
    if mtf_bias == "BULL":
        score += 2
        reasons.append("MTF bias: higher timeframes BULLISH (+2)")
    elif mtf_bias == "BEAR":
        score -= 2
        reasons.append("MTF bias: higher timeframes BEARISH (-2)")

    # Block counter-trend entries when bias is strong
    if mtf_bias == "BULL" and score <= -4:
        score = -3
        reasons.append("SHORT blocked — higher TF trend is bullish")
    elif mtf_bias == "BEAR" and score >= 4:
        score = 3
        reasons.append("LONG blocked — higher TF trend is bearish")

    direction = "LONG" if score >= 4 else "SHORT" if score <= -4 else "NEUTRAL"

    atr_val = ind["atr"]
    price   = ind["price"]
    sl_mult, tp_mult = 1.5, 3.0

    if direction == "LONG":
        sl     = round(price - sl_mult * atr_val, 2)
        tp     = round(price + tp_mult * atr_val, 2)
        sl_pct = round((price - sl) / price * 100, 2)
        tp_pct = round((tp - price) / price * 100, 2)
    elif direction == "SHORT":
        sl     = round(price + sl_mult * atr_val, 2)
        tp     = round(price - tp_mult * atr_val, 2)
        sl_pct = round((sl - price) / price * 100, 2)
        tp_pct = round((price - tp) / price * 100, 2)
    else:
        sl = tp = sl_pct = tp_pct = None

    return {
        "direction": direction,
        "score":     score,
        "entry":     price,
        "tp":        tp,
        "sl":        sl,
        "tp_pct":    tp_pct,
        "sl_pct":    sl_pct,
        "reasons":   reasons,
    }


# ── Multi-Timeframe Analysis ──────────────────────────────────────────────────

def mtf_bias(coin: str, demo: bool, demo_seeds: dict) -> tuple[str, dict[str, int]]:
    """
    Analyse 4h and 1d timeframes and return an overall bias:
      BULL  — both higher TFs lean bullish (score > 0)
      BEAR  — both higher TFs lean bearish (score < 0)
      NEUTRAL — mixed or flat
    Also returns per-timeframe scores for display.
    """
    symbol  = SYMBOLS[coin]
    tf_scores: dict[str, int] = {}

    for tf in ["4h", "1d"]:
        try:
            if demo:
                df = generate_demo_ohlcv(demo_seeds[coin], seed=demo_seeds[coin] + hash(tf) % 100)
            else:
                df = fetch_ohlcv(symbol, interval=tf)
            ind = compute_indicators(df)
            s, _ = score_indicators(ind)
            tf_scores[tf] = s
        except Exception:
            tf_scores[tf] = 0

    bull_count = sum(1 for s in tf_scores.values() if s > 0)
    bear_count = sum(1 for s in tf_scores.values() if s < 0)

    if bull_count == len(tf_scores):
        bias = "BULL"
    elif bear_count == len(tf_scores):
        bias = "BEAR"
    else:
        bias = "NEUTRAL"

    return bias, tf_scores


# ── Position Size Calculator ──────────────────────────────────────────────────

def calc_position_size(
    balance: float,
    risk_pct: float,
    entry: float,
    sl: float,
    leverage: int = 1,
) -> dict:
    """
    Fixed-percentage risk model:
      risk_amount  = balance × risk_pct / 100
      sl_distance  = |entry - sl| / entry  (as a fraction)
      position_usd = risk_amount / sl_distance
      contracts    = position_usd / entry
      notional     = contracts × entry
      margin_req   = notional / leverage
    """
    risk_amount  = balance * risk_pct / 100
    sl_distance  = abs(entry - sl) / entry
    if sl_distance == 0:
        return {}
    position_usd = risk_amount / sl_distance
    contracts    = position_usd / entry
    notional     = contracts * entry
    margin_req   = notional / leverage

    return {
        "risk_amount":  round(risk_amount, 2),
        "position_usd": round(position_usd, 2),
        "contracts":    round(contracts, 6),
        "notional":     round(notional, 2),
        "margin_req":   round(margin_req, 2),
        "leverage":     leverage,
    }


# ── Display ───────────────────────────────────────────────────────────────────

def direction_color(d: str) -> str:
    return {"LONG": Fore.GREEN, "SHORT": Fore.RED, "NEUTRAL": Fore.YELLOW}.get(d, Fore.WHITE)

def bias_color(b: str) -> str:
    return {"BULL": Fore.GREEN, "BEAR": Fore.RED, "NEUTRAL": Fore.YELLOW}.get(b, Fore.WHITE)

def strength_bar(score: int) -> str:
    filled = min(abs(score), 10)
    bar    = "█" * filled + "░" * (10 - filled)
    color  = Fore.GREEN if score > 0 else Fore.RED if score < 0 else Fore.YELLOW
    return f"{color}[{bar}]{Style.RESET_ALL} {score:+d}"

def score_symbol(score: int) -> str:
    if score > 0:   return f"{Fore.GREEN}▲ {score:+d}{Style.RESET_ALL}"
    if score < 0:   return f"{Fore.RED}▼ {score:+d}{Style.RESET_ALL}"
    return f"{Fore.YELLOW}● {score:+d}{Style.RESET_ALL}"


def print_analysis(
    coin: str,
    ind: dict,
    sig: dict,
    primary_tf: str,
    bias: str,
    tf_scores: dict[str, int],
    pos: dict | None = None,
):
    d   = direction_color(sig["direction"])
    bc  = bias_color(bias)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n╔{'═' * 62}╗")
    print(f"║  {Fore.CYAN}{Style.BRIGHT}{coin}/USDT PERP{Style.RESET_ALL}  │  ${ind['price']:,.2f}  │  {now}")
    print(f"╠{'═' * 62}╣")

    # MTF summary row
    tf_line = "  ".join(
        f"{tf}: {score_symbol(tf_scores.get(tf, 0))}" for tf in ["4h", "1d"]
    )
    print(f"║  Higher TF bias: {bc}{Style.BRIGHT}{bias:7s}{Style.RESET_ALL}   ({tf_line})")
    print(f"║  Primary ({primary_tf}):  {d}{Style.BRIGHT}{sig['direction']:7s}{Style.RESET_ALL}  {strength_bar(sig['score'])}")
    print(f"╠{'─' * 62}╣")

    if sig["direction"] != "NEUTRAL":
        print(f"║  Entry : {Style.BRIGHT}${sig['entry']:>12,.2f}{Style.RESET_ALL}")
        print(f"║  {Fore.GREEN}TP    : ${sig['tp']:>12,.2f}  (+{sig['tp_pct']:.2f}%){Style.RESET_ALL}")
        print(f"║  {Fore.RED}SL    : ${sig['sl']:>12,.2f}  (-{sig['sl_pct']:.2f}%){Style.RESET_ALL}")
        rr = sig["tp_pct"] / sig["sl_pct"] if sig["sl_pct"] else 0
        print(f"║  R/R   : 1 : {rr:.1f}")

        if pos:
            print(f"╠{'─' * 62}╣")
            print(f"║  {Style.BRIGHT}Position Size  (balance=${pos['risk_amount']/( (pos.get('risk_pct') or 1)/100):,.0f}, "
                  f"risk={pos.get('risk_pct', '?')}%, {pos['leverage']}x leverage):{Style.RESET_ALL}")
            print(f"║    Risk amount : ${pos['risk_amount']:,.2f}")
            print(f"║    Position    : ${pos['position_usd']:,.2f}  ({pos['contracts']} contracts)")
            print(f"║    Notional    : ${pos['notional']:,.2f}")
            print(f"║    Margin req  : ${pos['margin_req']:,.2f}")
    else:
        print(f"║  No trade — wait for a clearer setup.")

    print(f"╠{'─' * 62}╣")
    print(f"║  {Style.BRIGHT}Indicators ({primary_tf}):{Style.RESET_ALL}")
    print(f"║    RSI(14)        : {ind['rsi']:.1f}")
    print(f"║    Stoch RSI      : {ind['stoch_rsi']:.1f}")
    print(f"║    MACD hist      : {ind['macd_hist']:.5f}")
    print(f"║    BB upper/lower : ${ind['bb_upper']:,.2f} / ${ind['bb_lower']:,.2f}  (width {ind['bb_width_pct']:.1f}%)")
    print(f"║    EMA 20/50/200  : ${ind['ema20']:,.2f} / ${ind['ema50']:,.2f} / ${ind['ema200']:,.2f}")
    print(f"║    ATR(14)        : ${ind['atr']:,.2f}")
    print(f"║    Volume ratio   : {ind['vol_ratio']:.2f}x  (vs 15-candle avg)")
    print(f"║    ROC(10)        : {ind['roc_10']:+.2f}%")

    print(f"╠{'─' * 62}╣")
    print(f"║  {Style.BRIGHT}Signal reasons:{Style.RESET_ALL}")
    for r in sig["reasons"]:
        print(f"║    • {r}")
    print(f"╚{'═' * 62}╝\n")


def print_alert(coin: str, sig: dict, prev: str, bias: str):
    d  = direction_color(sig["direction"])
    bc = bias_color(bias)
    print(f"\n{'▶' * 3} {Fore.YELLOW}{Style.BRIGHT}SIGNAL ALERT{Style.RESET_ALL} {'◀' * 3}")
    print(f"  {Fore.CYAN}{coin}/USDT PERP{Style.RESET_ALL}  {prev} → {d}{Style.BRIGHT}{sig['direction']}{Style.RESET_ALL}  │  MTF: {bc}{bias}{Style.RESET_ALL}")
    print(f"  Entry: ${sig['entry']:,.2f}")
    if sig["tp"]:
        print(f"  {Fore.GREEN}TP: ${sig['tp']:,.2f}  (+{sig['tp_pct']:.2f}%){Style.RESET_ALL}   "
              f"{Fore.RED}SL: ${sig['sl']:,.2f}  (-{sig['sl_pct']:.2f}%){Style.RESET_ALL}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'▶' * 40}\n")


# ── Analysis Runner ───────────────────────────────────────────────────────────

DEMO_SEEDS = {"BTC": 107000, "ETH": 2520}


def analyze(
    coins: list,
    interval: str,
    balance: float | None,
    risk_pct: float,
    leverage: int,
    verbose: bool,
    demo: bool,
    use_mtf: bool = True,
) -> dict:
    results = {}

    for coin in coins:
        symbol = SYMBOLS[coin]
        try:
            # Primary timeframe data
            if demo:
                df = generate_demo_ohlcv(DEMO_SEEDS[coin])
                if verbose:
                    print(f"{Fore.YELLOW}[DEMO]{Style.RESET_ALL} Synthetic data for {coin}")
            else:
                df = fetch_ohlcv(symbol, interval=interval)

            ind = compute_indicators(df)

            # Multi-timeframe bias (skip if primary is already the highest TF)
            if use_mtf and interval not in ("4h", "1d"):
                bias, tf_scores = mtf_bias(coin, demo, DEMO_SEEDS)
            else:
                bias, tf_scores = "NEUTRAL", {}

            sig = generate_signal(ind, mtf_bias=bias)

            # Position sizing
            pos = None
            if balance and sig["sl"] is not None:
                pos = calc_position_size(balance, risk_pct, sig["entry"], sig["sl"], leverage)
                pos["risk_pct"] = risk_pct

            results[coin] = (ind, sig, bias, tf_scores, pos)

            if verbose:
                print_analysis(coin, ind, sig, interval, bias, tf_scores, pos)

        except requests.HTTPError as e:
            print(f"{Fore.RED}HTTP error fetching {coin}: {e}{Style.RESET_ALL}")
        except requests.ConnectionError:
            print(f"{Fore.RED}Cannot reach Bitunix. Check connection or use --demo.{Style.RESET_ALL}")
        except ValueError as e:
            print(f"{Fore.RED}{e}{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}Error analyzing {coin}: {e}{Style.RESET_ALL}")

    return results


def monitor_loop(
    coins: list,
    interval: str,
    refresh: int,
    balance: float | None,
    risk_pct: float,
    leverage: int,
    demo: bool,
):
    print(f"{Fore.CYAN}Monitoring {', '.join(coins)} on {interval} with MTF confirmation.{Style.RESET_ALL}")
    print(f"Refresh every {refresh}s. Press Ctrl+C to stop.\n")
    prev: dict[str, str] = {}

    while True:
        results = analyze(coins, interval, balance, risk_pct, leverage, verbose=True, demo=demo)
        for coin, (ind, sig, bias, tf_scores, pos) in results.items():
            p = prev.get(coin, "NEUTRAL")
            if sig["direction"] != "NEUTRAL" and sig["direction"] != p:
                print_alert(coin, sig, p, bias)
            prev[coin] = sig["direction"]

        print(f"{Fore.WHITE}Next refresh in {refresh}s  [{datetime.now().strftime('%H:%M:%S')}]{Style.RESET_ALL}")
        try:
            time.sleep(refresh)
        except KeyboardInterrupt:
            print("\nStopped.")
            sys.exit(0)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BTC/ETH Bitunix Futures — LONG/SHORT signals with MTF + position sizing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python crypto_analyzer.py                               # BTC+ETH, MTF, no sizing
  python crypto_analyzer.py --balance 10000 --risk 1     # 1% risk on $10k account
  python crypto_analyzer.py --balance 5000 --risk 2 --leverage 5
  python crypto_analyzer.py --coins BTC --interval 4h    # single TF, no MTF
  python crypto_analyzer.py --monitor --refresh 300      # continuous mode
  python crypto_analyzer.py --demo                       # offline test
        """
    )
    parser.add_argument("--coins",    nargs="+", choices=["BTC", "ETH"], default=["BTC", "ETH"])
    parser.add_argument("--interval", default="1h", choices=list(INTERVAL_MAP.keys()))
    parser.add_argument("--balance",  type=float, default=None,
                        help="Account balance in USDT for position sizing")
    parser.add_argument("--risk",     type=float, default=1.0,
                        help="Risk per trade as %% of balance (default: 1.0)")
    parser.add_argument("--leverage", type=int,   default=1,
                        help="Futures leverage for margin calculation (default: 1)")
    parser.add_argument("--monitor",  action="store_true")
    parser.add_argument("--refresh",  type=int,   default=300)
    parser.add_argument("--demo",     action="store_true")
    args = parser.parse_args()

    api_key = os.getenv("BITUNIX_API_KEY", "")
    if api_key:
        print(f"{Fore.GREEN}Bitunix API key loaded.{Style.RESET_ALL}")

    if args.monitor:
        monitor_loop(args.coins, args.interval, args.refresh,
                     args.balance, args.risk, args.leverage, args.demo)
    else:
        analyze(args.coins, args.interval, args.balance, args.risk,
                args.leverage, verbose=True, demo=args.demo)


if __name__ == "__main__":
    main()
