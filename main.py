import json
import os
import time
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import requests
import yfinance as yf


SIGNAL_HISTORY_FILE = "signal_history.json"
UNIVERSE_CACHE_FILE = "universe_cache.json"

SCAN_INTERVAL_SECONDS = 600

EXTRA_ETFS = [
    "SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT",
    "XLF", "XLK", "XLE", "XLV", "XLY", "XLI", "XLP",
    "XLU", "XLB", "XLRE", "SMH"
]

SECTOR_MAP = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLY": "Consumer Discretionary",
    "XLI": "Industrials",
    "XLP": "Consumer Staples",
    "XLU": "Utilities",
    "XLB": "Materials",
    "XLRE": "Real Estate"
}

YAHOO_SECTOR_TO_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Industrials": "XLI",
    "Consumer Defensive": "XLP",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
}


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


def get_number(value):
    return float(value.squeeze())


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


def get_sector_returns():
    sector_returns = {}

    for etf in SECTOR_MAP:
        try:
            data = yf.download(
                etf,
                period="4mo",
                interval="1d",
                progress=False,
                auto_adjust=True
            )

            if data.empty or len(data) < 63:
                continue

            closes = data["Close"].squeeze()
            start = float(closes.iloc[-63])
            end = float(closes.iloc[-1])

            if start > 0:
                sector_returns[etf] = (end - start) / start

            time.sleep(0.1)

        except Exception:
            pass

    return sector_returns


def get_ticker_sector_etf(ticker):
    try:
        info = yf.Ticker(ticker).info
        sector = info.get("sector", "")
        return YAHOO_SECTOR_TO_ETF.get(sector)
    except Exception:
        return None


def score_buy_signal(signal, data, spy_12m_return, sector_returns):
    score = 0
    breakdown = []

    close = signal["daily_close"]
    closes = data["Close"].squeeze()
    highs = data["High"].squeeze()
    volumes = data["Volume"].squeeze()

    if signal["system"] == "SYSTEM 2":
        score += 30
        breakdown.append("55-day breakout +30")
    else:
        score += 10
        breakdown.append("20-day breakout +10")

    if len(closes) >= 200:
        ma200 = float(closes.iloc[-200:].mean())
        if close > ma200:
            score += 15
            breakdown.append("above 200 MA +15")

    if len(closes) >= 50:
        ma50 = float(closes.iloc[-50:].mean())
        if close > ma50:
            score += 10
            breakdown.append("above 50 MA +10")

    if len(highs) >= 252:
        high_52w = float(highs.iloc[-252:].max())
    else:
        high_52w = float(highs.max())

    if close >= high_52w * 0.99:
        score += 15
        breakdown.append("near/new 52-week high +15")

    if spy_12m_return is not None and len(closes) >= 252:
        stock_start = float(closes.iloc[-252])

        if stock_start > 0:
            stock_12m_return = (close - stock_start) / stock_start
            rs_diff = stock_12m_return - spy_12m_return

            if rs_diff >= 0.30:
                score += 20
                breakdown.append(f"relative strength vs SPY +{round(rs_diff * 100, 1)}% +20")
            elif rs_diff >= 0.15:
                score += 15
                breakdown.append(f"relative strength vs SPY +{round(rs_diff * 100, 1)}% +15")
            elif rs_diff >= 0.05:
                score += 10
                breakdown.append(f"relative strength vs SPY +{round(rs_diff * 100, 1)}% +10")
            elif rs_diff >= 0:
                score += 5
                breakdown.append(f"relative strength vs SPY +{round(rs_diff * 100, 1)}% +5")

    if len(volumes) >= 20:
        avg_volume_20 = float(volumes.iloc[-20:].mean())
        today_volume = float(volumes.iloc[-1])

        if avg_volume_20 > 0 and today_volume >= avg_volume_20 * 1.5:
            score += 10
            breakdown.append(f"volume surge {round(today_volume / avg_volume_20, 1)}x +10")

    if sector_returns:
        sector_etf = get_ticker_sector_etf(signal["ticker"])

        if sector_etf and sector_etf in sector_returns:
            sorted_sector_returns = sorted(sector_returns.values(), reverse=True)

            top3_threshold = (
                sorted_sector_returns[2]
                if len(sorted_sector_returns) >= 3
                else sorted_sector_returns[-1]
            )

            if sector_returns[sector_etf] >= top3_threshold:
                score += 10
                sector_name = SECTOR_MAP.get(sector_etf, sector_etf)
                breakdown.append(f"leading sector {sector_name} +10")

    score = min(score, 100)

    if score >= 90:
        grade = "PRIME"
        grade_emoji = "🔥"
    elif score >= 80:
        grade = "EXCELLENT"
        grade_emoji = "⭐"
    else:
        return None

    return {
        "score": score,
        "grade": grade,
        "grade_emoji": grade_emoji,
        "breakdown": breakdown,
    }


def scan_ticker(ticker, spy_12m_return, sector_returns):
    data = yf.download(
        ticker,
        period="2y",
        interval="1d",
        progress=False,
        auto_adjust=True
    )

    if data.empty or len(data) < 252:
        return []

    close = get_number(data["Close"].iloc[-1])
    candle_date = data.index[-1].strftime("%Y-%m-%d")

    high_20 = get_number(data["High"].iloc[-21:-1].max())
    high_55 = get_number(data["High"].iloc[-56:-1].max())

    actionable_signals = []

    turtle_buy_signals = []

    if close > high_20:
        turtle_buy_signals.append({
            "ticker": ticker,
            "system": "SYSTEM 1",
            "type": "BUY",
            "rule": "20-day high breakout",
            "daily_close": round(close, 2),
            "breakout_level": round(high_20, 2),
            "candle_date": candle_date,
        })

    if close > high_55:
        turtle_buy_signals.append({
            "ticker": ticker,
            "system": "SYSTEM 2",
            "type": "BUY",
            "rule": "55-day high breakout",
            "daily_close": round(close, 2),
            "breakout_level": round(high_55, 2),
            "candle_date": candle_date,
        })

    for signal in turtle_buy_signals:
        scoring = score_buy_signal(signal, data, spy_12m_return, sector_returns)

        if scoring is None:
            continue

        signal.update(scoring)
        actionable_signals.append(signal)

    return actionable_signals


def signal_key(signal):
    return f"{signal['candle_date']}|{signal['ticker']}|{signal['system']}|{signal['type']}|{signal['rule']}|{signal['grade']}"


def print_signal(signal):
    print("", flush=True)
    print(f"🟢 🐢 {signal['system']} BUY", flush=True)
    print(f"{signal['grade_emoji']} Grade: {signal['grade']} | Score: {signal['score']}/100", flush=True)
    print(f"Ticker:          {signal['ticker']}", flush=True)
    print(f"Rule:            {signal['rule']}", flush=True)
    print(f"Daily Close:     {signal['daily_close']}", flush=True)
    print(f"Breakout Level:  {signal['breakout_level']}", flush=True)
    print(f"Candle Date:     {signal['candle_date']}", flush=True)

    if signal.get("breakdown"):
        print(f"Scoring:         {' | '.join(signal['breakdown'])}", flush=True)

    print("", flush=True)


def run_scan():
    print("===================================", flush=True)
    print("🐢 Turtle Trade Scanner - ACTIONABLE ONLY", flush=True)
    print(f"Run time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
    print("===================================", flush=True)

    tickers = get_market_tickers()
    history = load_signal_history()

    print("Fetching SPY 12-month return...", flush=True)
    spy_12m_return = get_spy_12m_return()

    if spy_12m_return is not None:
        print(f"SPY 12m return: {round(spy_12m_return * 100, 1)}%", flush=True)

    print("Fetching sector returns...", flush=True)
    sector_returns = get_sector_returns()

    if sector_returns:
        top_sector = max(sector_returns, key=sector_returns.get)
        print(
            f"Leading sector: {SECTOR_MAP.get(top_sector, top_sector)} "
            f"({round(sector_returns[top_sector] * 100, 1)}%)",
            flush=True,
        )

    prime_count = 0
    excellent_count = 0
    new_actionable_signals = []
    total_actionable_signals = 0

    print(f"Scanning {len(tickers)} tickers...", flush=True)

    for ticker in tickers:
        try:
            signals = scan_ticker(ticker, spy_12m_return, sector_returns)

            for signal in signals:
                total_actionable_signals += 1

                if signal["grade"] == "PRIME":
                    prime_count += 1
                elif signal["grade"] == "EXCELLENT":
                    excellent_count += 1

                key = signal_key(signal)

                if key not in history:
                    history[key] = signal
                    new_actionable_signals.append(signal)
                    print_signal(signal)

            time.sleep(0.15)

        except Exception as error:
            print(f"Error scanning {ticker}: {error}", flush=True)

    save_signal_history(history)

    print("===================================", flush=True)
    print(f"Stocks scanned:             {len(tickers)}", flush=True)
    print(f"Total actionable signals:   {total_actionable_signals}", flush=True)
    print(f"New actionable signals:     {len(new_actionable_signals)}", flush=True)
    print("-----------------------------------", flush=True)
    print(f"🔥 PRIME:                   {prime_count}", flush=True)
    print(f"⭐ EXCELLENT:               {excellent_count}", flush=True)
    print("===================================", flush=True)


def seconds_until_next_scan():
    return SCAN_INTERVAL_SECONDS


print("🐢 Turtle Trade Scanner Started - ACTIONABLE ONLY TEST MODE", flush=True)

run_scan()

while True:
    sleep_seconds = seconds_until_next_scan()
    print(f"Sleeping until next scan in {round(sleep_seconds / 60, 1)} minutes...", flush=True)
    time.sleep(sleep_seconds)
    run_scan()
