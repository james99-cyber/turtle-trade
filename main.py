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
    headers = {"User-Agent": "Mozilla/5.0 TurtleTradeBot/1.0"}
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
        json.dump(
            {
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "tickers": tickers,
            },
            file,
            indent=2,
        )


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

    clean_tickers = sorted(
        [
            str(ticker).replace(".", "-").strip()
            for ticker in tickers
            if isinstance(ticker, str) and ticker.strip()
        ]
    )

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


def get_spy_12m_return():
    try:
        spy = yf.download(
            "SPY",
            period="13mo",
            interval="1d",
            progress=False,
            auto_adjust=True
        )

        if spy.empty or len(spy) < 252:
            return None

        closes = spy["Close"].squeeze()
        spy_start = float(closes.iloc[-252])
        spy_end = float(closes.iloc[-1])

        if spy_start <= 0:
            return None

        return (spy_end - spy_start) / spy_start

    except Exception as error:
        print(f"Could not calculate SPY 12m return: {error}", flush=True)
        return None


def check_prime_turtle_signal(ticker, spy_12m_return, diagnostics):
    data = yf.download(
        ticker,
        period="2y",
        interval="1d",
        progress=False,
        auto_adjust=True
    )

    if data.empty or len(data) < 252:
        return None

    closes = data["Close"].squeeze()
    highs = data["High"].squeeze()
    volumes = data["Volume"].squeeze()

    close = float(closes.iloc[-1])
    candle_date = data.index[-1].strftime("%Y-%m-%d")

    high_55 = float(highs.iloc[-56:-1].max())
    high_52w = float(highs.iloc[-252:].max())

    ma_200 = float(closes.iloc[-200:].mean())
    ma_50 = float(closes.iloc[-50:].mean())

    avg_volume_20 = float(volumes.iloc[-20:].mean())
    today_volume = float(volumes.iloc[-1])

    if close <= high_55:
        return None

    diagnostics["55_day_breakout"] += 1

    if close <= ma_200:
        return None

    diagnostics["above_200_ma"] += 1

    if close <= ma_50:
        return None

    diagnostics["above_50_ma"] += 1

    if close < high_52w * 0.99:
        return None

    diagnostics["near_52_week_high"] += 1

    if spy_12m_return is None:
        return None

    stock_start = float(closes.iloc[-252])

    if stock_start <= 0:
        return None

    stock_12m_return = (close - stock_start) / stock_start
    rs_diff = stock_12m_return - spy_12m_return

    if rs_diff < 0.15:
        return None

    diagnostics["rs_above_15"] += 1

    if avg_volume_20 <= 0:
        return None

    volume_ratio = today_volume / avg_volume_20

    if volume_ratio < 1.5:
        return None

    diagnostics["volume_above_1_5"] += 1

    return {
        "ticker": ticker,
        "type": "PRIME_TURTLE_BUY",
        "rule": "System 2 55-day breakout with Prime filters",
        "daily_close": round(close, 2),
        "breakout_level": round(high_55, 2),
        "ma_200": round(ma_200, 2),
        "ma_50": round(ma_50, 2),
        "high_52w": round(high_52w, 2),
        "rs_vs_spy": round(rs_diff * 100, 1),
        "volume_ratio": round(volume_ratio, 2),
        "candle_date": candle_date,
    }


def signal_key(signal):
    return f"{signal['candle_date']}|{signal['ticker']}|{signal['type']}|{signal['rule']}"


def print_prime_signal(signal):
    print("", flush=True)
    print("🚨 🐢 PRIME TURTLE SIGNAL", flush=True)
    print(f"Ticker:          {signal['ticker']}", flush=True)
    print(f"Rule:            {signal['rule']}", flush=True)
    print(f"Daily Close:     {signal['daily_close']}", flush=True)
    print(f"55-Day Level:    {signal['breakout_level']}", flush=True)
    print(f"200 MA:          {signal['ma_200']}", flush=True)
    print(f"50 MA:           {signal['ma_50']}", flush=True)
    print(f"52-Week High:    {signal['high_52w']}", flush=True)
    print(f"RS vs SPY:       +{signal['rs_vs_spy']}%", flush=True)
    print(f"Volume:          {signal['volume_ratio']}x 20-day avg", flush=True)
    print(f"Candle Date:     {signal['candle_date']}", flush=True)
    print("", flush=True)


def is_weekend():
    now = datetime.now(timezone.utc)
    return now.weekday() >= 5


def run_scan():
    print("===================================", flush=True)
    print("🐢 Turtle Trade PRIME Scanner", flush=True)
    print(f"Run time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
    print("===================================", flush=True)

    if is_weekend():
        print("Weekend detected - skipping scan.", flush=True)
        return

    tickers = get_market_tickers()
    history = load_signal_history()

    print("Fetching SPY 12-month return...", flush=True)
    spy_12m_return = get_spy_12m_return()

    if spy_12m_return is not None:
        print(f"SPY 12m return: {round(spy_12m_return * 100, 1)}%", flush=True)

    print(f"Scanning {len(tickers)} tickers for PRIME signals...", flush=True)

    diagnostics = {
        "55_day_breakout": 0,
        "above_200_ma": 0,
        "above_50_ma": 0,
        "near_52_week_high": 0,
        "rs_above_15": 0,
        "volume_above_1_5": 0,
    }

    prime_signals = []
    new_prime_signals = []

    for ticker in tickers:
        try:
            signal = check_prime_turtle_signal(ticker, spy_12m_return, diagnostics)

            if signal is None:
                time.sleep(0.1)
                continue

            prime_signals.append(signal)
            key = signal_key(signal)

            if key not in history:
                history[key] = signal
                new_prime_signals.append(signal)
                print_prime_signal(signal)

            time.sleep(0.1)

        except Exception as error:
            print(f"Error scanning {ticker}: {error}", flush=True)

    save_signal_history(history)

    print("===================================", flush=True)
    print(f"Stocks scanned:          {len(tickers)}", flush=True)
    print("-----------------------------------", flush=True)
    print("Filter diagnostics:", flush=True)
    print(f"55-day breakout:         {diagnostics['55_day_breakout']}", flush=True)
    print(f"Above 200 MA:            {diagnostics['above_200_ma']}", flush=True)
    print(f"Above 50 MA:             {diagnostics['above_50_ma']}", flush=True)
    print(f"Near 52-week high:       {diagnostics['near_52_week_high']}", flush=True)
    print(f"RS vs SPY > 15%:         {diagnostics['rs_above_15']}", flush=True)
    print(f"Volume > 1.5x avg:       {diagnostics['volume_above_1_5']}", flush=True)
    print("-----------------------------------", flush=True)
    print(f"Prime signals found:     {len(prime_signals)}", flush=True)
    print(f"New prime signals:       {len(new_prime_signals)}", flush=True)
    print("===================================", flush=True)

    if len(prime_signals) == 0:
        print("No PRIME Turtle signals found this scan.", flush=True)


def seconds_until_next_scan():
    now = datetime.now(timezone.utc)

    target = now.replace(
        hour=22,
        minute=15,
        second=0,
        microsecond=0
    )

    if now >= target:
        target = target + timedelta(days=1)

    while target.weekday() >= 5:
        target = target + timedelta(days=1)

    return max(60, int((target - now).total_seconds()))


print("🐢 Turtle Trade Scanner Started - DAILY PRIME MODE", flush=True)

run_scan()

while True:
    sleep_seconds = seconds_until_next_scan()
    print(f"Sleeping until next scan in {round(sleep_seconds / 3600, 2)} hours...", flush=True)
    time.sleep(sleep_seconds)
    run_scan()
