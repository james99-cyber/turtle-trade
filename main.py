import json
import os
import time
from datetime import datetime, timezone

import yfinance as yf


TICKERS = [
    "SPY", "QQQ", "GLD", "SLV",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "TSLA", "AMD", "AVGO", "PLTR", "NFLX", "COST",
    "JPM", "V", "MA", "UNH", "LLY", "XOM"
]

SIGNAL_HISTORY_FILE = "signal_history.json"


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

    high_20 = get_number(data["High"].iloc[-21:-1].max())
    high_55 = get_number(data["High"].iloc[-56:-1].max())

    low_10 = get_number(data["Low"].iloc[-11:-1].min())
    low_20 = get_number(data["Low"].iloc[-21:-1].min())

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    signals = []

    if close > high_20:
        signals.append({
            "ticker": ticker,
            "system": "SYSTEM 1",
            "type": "BUY",
            "rule": "20-day high breakout",
            "price": round(close, 2),
            "level": round(high_20, 2),
            "date": today
        })

    if close < low_10:
        signals.append({
            "ticker": ticker,
            "system": "SYSTEM 1",
            "type": "SELL",
            "rule": "10-day low breakout",
            "price": round(close, 2),
            "level": round(low_10, 2),
            "date": today
        })

    if close > high_55:
        signals.append({
            "ticker": ticker,
            "system": "SYSTEM 2",
            "type": "BUY",
            "rule": "55-day high breakout",
            "price": round(close, 2),
            "level": round(high_55, 2),
            "date": today
        })

    if close < low_20:
        signals.append({
            "ticker": ticker,
            "system": "SYSTEM 2",
            "type": "SELL",
            "rule": "20-day low breakout",
            "price": round(close, 2),
            "level": round(low_20, 2),
            "date": today
        })

    return signals


def signal_key(signal):
    return f"{signal['date']}|{signal['ticker']}|{signal['system']}|{signal['type']}|{signal['rule']}"


def print_signal(signal):
    emoji = "🟢" if signal["type"] == "BUY" else "🔴"

    print("", flush=True)
    print(f"{emoji} 🐢 {signal['system']} {signal['type']}", flush=True)
    print(f"Ticker: {signal['ticker']}", flush=True)
    print(f"Rule: {signal['rule']}", flush=True)
    print(f"Price: {signal['price']}", flush=True)
    print(f"Breakout Level: {signal['level']}", flush=True)
    print(f"Date: {signal['date']}", flush=True)
    print("", flush=True)


def run_scan():
    print("===================================", flush=True)
    print("🐢 Turtle Trade Daily Scanner", flush=True)
    print(f"Run time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
    print("===================================", flush=True)

    history = load_signal_history()
    new_signals = []
    total_signals = 0

    for ticker in TICKERS:
        try:
            signals = scan_ticker(ticker)

            if not signals:
                print(f"No signal: {ticker}", flush=True)
                continue

            for signal in signals:
                total_signals += 1
                key = signal_key(signal)

                if key not in history:
                    history[key] = signal
                    new_signals.append(signal)
                    print_signal(signal)
                else:
                    print(f"Already recorded: {signal['ticker']} {signal['system']} {signal['type']}", flush=True)

        except Exception as error:
            print(f"Error scanning {ticker}: {error}", flush=True)

    save_signal_history(history)

    print("-----------------------------------", flush=True)
    print(f"Stocks scanned: {len(TICKERS)}", flush=True)
    print(f"Total signals found today: {total_signals}", flush=True)
    print(f"New signals recorded: {len(new_signals)}", flush=True)
    print("-----------------------------------", flush=True)


def seconds_until_next_scan():
    # Runs once per day at 22:15 UTC.
    # This is after the normal US market close.
    now = datetime.now(timezone.utc)
    target = now.replace(hour=22, minute=15, second=0, microsecond=0)

    if now >= target:
        target = target.replace(day=target.day + 1)

    return max(60, int((target - now).total_seconds()))


print("🐢 Turtle Trade Scanner Started - PURE TURTLE RULES", flush=True)

run_scan()

while True:
    sleep_seconds = seconds_until_next_scan()
    print(f"Sleeping until next daily scan in {round(sleep_seconds / 3600, 2)} hours...", flush=True)
    time.sleep(sleep_seconds)
    run_scan()
