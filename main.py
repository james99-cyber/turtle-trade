import json
import os
import time
from datetime import datetime, timezone, timedelta
from io import StringIO

import pandas as pd
import requests
import yfinance as yf


SIGNAL_HISTORY_FILE = "signal_history.json"
UNIVERSE_CACHE_FILE = "universe_cache.json"

EXTRA_ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT",
    "XLF", "XLK", "XLE", "XLV", "XLY", "XLI", "XLP",
    "XLU", "XLB", "XLRE", "SMH"
]


def fetch_html_tables(url):
    headers = {
        "User-Agent": "Mozilla/5.0 TurtleTradeBot/1.0"
    }
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return pd.read_html(StringIO(response.text))


def load_cached_universe():
    if not os.path.exists(UNIVERSE_CACHE_FILE):
        return []

    try:
        with open(UNIVERSE_CACHE_FILE, "r") as file:
            cache = json.load(file)
            return cache.get("tickers", [])
    except Exception:
        return []


def save_universe(tickers):
    with open(UNIVERSE_CACHE_FILE, "w") as file:
        json.dump({
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "tickers": tickers
        }, file, indent=2)


def get_market_tickers():
    tickers = set(EXTRA_ETFS)

    try:
        sp500_tables = fetch_html_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        sp500 = sp500_tables[0]
        tickers.update(sp500["Symbol"].tolist())
        print(f"Loaded S&P 500: {len(sp500)} symbols", flush=True)
    except Exception as error:
        print(f"Could not load S&P 500 list: {error}", flush=True)

    try:
        nasdaq_tables = fetch_html_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
        for table in nasdaq_tables:
            if "Ticker" in table.columns:
                tickers.update(table["Ticker"].tolist())
                print(f"Loaded Nasdaq 100: {len(table)} symbols", flush=True)
                break
    except Exception as error:
        print(f"Could not load Nasdaq 100 list: {error}", flush=True)

    clean_tickers = sorted([
        str(ticker).replace(".", "-").strip()
        for ticker in tickers
        if isinstance(ticker, str) and ticker.strip()
    ])

    if len(clean_tickers) > len(EXTRA_ETFS):
        save_universe(clean_tickers)
        return clean_tickers

    cached = load_cached_universe()
    if cached:
        print(f"Using cached universe: {len(cached)} symbols", flush=True)
        return cached

    print("Using ETF fallback only", flush=True)
    return sorted(EXTRA_ETFS)


def load_signal_history():
    if not os.path.exists(SIGNAL_HISTORY_FILE):
        return {}

    try:
        with open(SIGNAL_HISTORY_FILE, "r") as file:
            return json.load(file)
    except Exception:
        return {}


def save_signal_history(history):
    with open(SIGNAL_HISTORY_FILE, "w") as file:
        json.dump(history, file, indent=2)


def get_number(value):
    return float(value.squeeze())


def scan_ticker(ticker):
    data = yf.download(
        ticker,
        period="1y",
        interval="1d",
        progress=False,
        auto_adjust=True
    )

    if data.empty or len(data) < 60:
        return []

    close = get_number(data["Close"].iloc[-1])
    candle_date = data.index[-1].strftime("%Y-%m-%d")

    high_20 = get_number(data["High"].iloc[-21:-1].max())
    high_55 = get_number(data["High"].iloc[-56:-1].max())

    low_10 = get_number(data["Low"].iloc[-11:-1].min())
    low_20 = get_number(data["Low"].iloc[-21:-1].min())

    signals = []

    if close > high_20:
        signals.append({
            "ticker": ticker,
            "system": "SYSTEM 1",
            "type": "BUY",
            "rule": "20-day high breakout",
            "daily_close": round(close, 2),
            "breakout_level": round(high_20, 2),
            "candle_date": candle_date
        })

    if close < low_10:
        signals.append({
            "ticker": ticker,
            "system": "SYSTEM 1",
            "type": "SELL",
            "rule": "10-day low breakout",
            "daily_close": round(close, 2),
            "breakout_level": round(low_10, 2),
            "candle_date": candle_date
        })

    if close > high_55:
        signals.append({
            "ticker": ticker,
            "system": "SYSTEM 2",
            "type": "BUY",
            "rule": "55-day high breakout",
            "daily_close": round(close, 2),
            "breakout_level": round(high_55, 2),
            "candle_date": candle_date
        })

    if close < low_20:
        signals.append({
            "ticker": ticker,
            "system": "SYSTEM 2",
            "type": "SELL",
            "rule": "20-day low breakout",
            "daily_close": round(close, 2),
            "breakout_level": round(low_20, 2),
            "candle_date": candle_date
        })

    return signals


def signal_key(signal):
    return f"{signal['candle_date']}|{signal['ticker']}|{signal['system']}|{signal['type']}|{signal['rule']}"


def print_signal(signal):
    emoji = "🟢" if signal["type"] == "BUY" else "🔴"

    print("", flush=True)
    print(f"{emoji} 🐢 {signal['system']} {signal['type']}", flush=True)
    print(f"Ticker: {signal['ticker']}", flush=True)
    print(f"Rule: {signal['rule']}", flush=True)
    print(f"Daily Close: {signal['daily_close']}", flush=True)
    print(f"Breakout Level: {signal['breakout_level']}", flush=True)
    print(f"Candle Date: {signal['candle_date']}", flush=True)
    print("", flush=True)


def run_scan():
    print("===================================", flush=True)
    print("🐢 Turtle Trade Daily Scanner", flush=True)
    print(f"Run time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
    print("===================================", flush=True)

    tickers = get_market_tickers()
    history = load_signal_history()

    new_signals = []
    total_signals = 0

    print(f"Scanning {len(tickers)} tickers...", flush=True)

    for ticker in tickers:
        try:
            signals = scan_ticker(ticker)

            for signal in signals:
                total_signals += 1
                key = signal_key(signal)

                if key not in history:
                    history[key] = signal
                    new_signals.append(signal)
                    print_signal(signal)

            time.sleep(0.15)

        except Exception as error:
            print(f"Error scanning {ticker}: {error}", flush=True)

    save_signal_history(history)

    print("-----------------------------------", flush=True)
    print(f"Stocks scanned: {len(tickers)}", flush=True)
    print(f"Total signals found today: {total_signals}", flush=True)
    print(f"New signals recorded: {len(new_signals)}", flush=True)
    print("-----------------------------------", flush=True)


def seconds_until_next_scan():
    now = datetime.now(timezone.utc)
    target = now.replace(hour=22, minute=15, second=0, microsecond=0)

    if now >= target:
        target = target + timedelta(days=1)

    return max(60, int((target - now).total_seconds()))


print("🐢 Turtle Trade Scanner Started - LIVE UNIVERSE", flush=True)

run_scan()

while True:
    sleep_seconds = seconds_until_next_scan()
    print(f"Sleeping until next daily scan in {round(sleep_seconds / 3600, 2)} hours...", flush=True)
    time.sleep(sleep_seconds)
    run_scan()
