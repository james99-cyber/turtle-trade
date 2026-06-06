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


def get_spy_12m_return():
    try:
        spy = yf.download("SPY", period="13mo", interval="1d", progress=False, auto_adjust=True)
        if len(spy) < 252:
            return None
        spy_start = float(spy["Close"].iloc[-252])
        spy_end = float(spy["Close"].iloc[-1])
        return (spy_end - spy_start) / spy_start
    except Exception:
        return None


def get_sector_returns():
    sector_returns = {}
    for etf in SECTOR_MAP:
        try:
            data = yf.download(etf, period="4mo", interval="1d", progress=False, auto_adjust=True)
            if len(data) >= 63:
                start = float(data["Close"].iloc[-63])
                end = float(data["Close"].iloc[-1])
                sector_returns[etf] = (end - start) / start
            time.sleep(0.1)
        except Exception:
            pass
    return sector_returns


def get_ticker_sector(ticker):
    try:
        info = yf.Ticker(ticker).info
        sector = info.get("sector", "")
        sector_etf_map = {
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
        return sector_etf_map.get(sector, None)
    except Exception:
        return None


def score_signal(signal, data, spy_12m_return, sector_returns):
    score = 0
    breakdown = []
    close = signal["daily_close"]

    if signal["type"] != "BUY":
        return None

    if signal["system"] == "SYSTEM 2":
        score += 30
        breakdown.append("55-day breakout: +30")
    else:
        score += 10
        breakdown.append("20-day breakout: +10")

    try:
        closes = data["Close"].squeeze()
        highs = data["High"].squeeze()
        volumes = data["Volume"].squeeze()

        if len(closes) >= 200:
            ma200 = float(closes.iloc[-200:].mean())
            if close > ma200:
                score += 15
                breakdown.append(f"Above 200 MA ({round(ma200, 2)}): +15")

        if len(closes) >= 50:
            ma50 = float(closes.iloc[-50:].mean())
            if close > ma50:
                score += 10
                breakdown.append(f"Above 50 MA ({round(ma50, 2)}): +10")

        if len(highs) >= 252:
            high_52w = float(highs.iloc[-252:].max())
        else:
            high_52w = float(highs.max())
        if close >= high_52w * 0.99:
            score += 15
            breakdown.append(f"52-week high ({round(high_52w, 2)}): +15")

        if spy_12m_return is not None and len(closes) >= 252:
            stock_start = float(closes.iloc[-252])
            stock_12m_return = (close - stock_start) / stock_start
            rs_diff = stock_12m_return - spy_12m_return
            if rs_diff >= 0.30:
                score += 20
                breakdown.append(f"RS vs SPY +{round(rs_diff*100,1)}%: +20")
            elif rs_diff >= 0.15:
                score += 15
                breakdown.append(f"RS vs SPY +{round(rs_diff*100,1)}%: +15")
            elif rs_diff >= 0.05:
                score += 10
                breakdown.append(f"RS vs SPY +{round(rs_diff*100,1)}%: +10")
            elif rs_diff >= 0:
                score += 5
                breakdown.append(f"RS vs SPY +{round(rs_diff*100,1)}%: +5")
            else:
                breakdown.append(f"RS vs SPY {round(rs_diff*100,1)}%: +0")

        if len(volumes) >= 20:
            avg_volume_20 = float(volumes.iloc[-20:].mean())
            today_volume = float(volumes.iloc[-1])
            if avg_volume_20 > 0 and today_volume >= avg_volume_20 * 1.5:
                score += 10
                breakdown.append(f"Volume surge {round(today_volume/avg_volume_20,1)}x avg: +10")

        if sector_returns:
            ticker_sector_etf = get_ticker_sector(signal["ticker"])
            if ticker_sector_etf and ticker_sector_etf in sector_returns:
                sector_ret = sector_returns[ticker_sector_etf]
                sorted_sectors = sorted(sector_returns.values(), reverse=True)
                top3_threshold = sorted_sectors[2] if len(sorted_sectors) >= 3 else sorted_sectors[-1]
                if sector_ret >= top3_threshold:
                    score += 10
                    breakdown.append(f"Leading sector ({SECTOR_MAP.get(ticker_sector_etf, ticker_sector_etf)}): +10")

    except Exception as e:
        print(f"Scoring error for {signal['ticker']}: {e}", flush=True)

    if score >= 90:
        grade = "PRIME"
        grade_emoji = "💎"
    elif score >= 70:
        grade = "EXCELLENT"
        grade_emoji = "⭐"
    elif score >= 50:
        grade = "GOOD"
        grade_emoji = "✅"
    else:
        grade = "WATCHLIST"
        grade_emoji = "👀"

    return {
        "score": score,
        "grade": grade,
        "grade_emoji": grade_emoji,
        "breakdown": breakdown
    }


def get_number(value):
    return float(value.squeeze())


def scan_ticker(ticker, spy_12m_return, sector_returns):
    data = yf.download(
        ticker,
        period="2y",
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
        signal = {
            "ticker": ticker,
            "system": "SYSTEM 1",
            "type": "BUY",
            "rule": "20-day high breakout",
            "daily_close": round(close, 2),
            "breakout_level": round(high_20, 2),
            "candle_date": candle_date
        }
        scoring = score_signal(signal, data, spy_12m_return, sector_returns)
        if scoring:
            signal.update(scoring)
        signals.append(signal)

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
        signal = {
            "ticker": ticker,
            "system": "SYSTEM 2",
            "type": "BUY",
            "rule": "55-day high breakout",
            "daily_close": round(close, 2),
            "breakout_level": round(high_55, 2),
            "candle_date": candle_date
        }
        scoring = score_signal(signal, data, spy_12m_return, sector_returns)
        if scoring:
            signal.update(scoring)
        signals.append(signal)

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
    is_buy = signal["type"] == "BUY"
    emoji = "🟢" if is_buy else "🔴"
    grade_emoji = signal.get("grade_emoji", "")
    grade = signal.get("grade", "")
    score = signal.get("score", "")

    print("", flush=True)
    print(f"{emoji} 🐢 {signal['system']} {signal['type']}", flush=True)
    if is_buy and grade:
        print(f"{grade_emoji} Grade: {grade}  |  Score: {score}/100", flush=True)
    print(f"Ticker:          {signal['ticker']}", flush=True)
    print(f"Rule:            {signal['rule']}", flush=True)
    print(f"Daily Close:     {signal['daily_close']}", flush=True)
    print(f"Breakout Level:  {signal['breakout_level']}", flush=True)
    print(f"Candle Date:     {signal['candle_date']}", flush=True)
    if is_buy and signal.get("breakdown"):
        print(f"Scoring:         {' | '.join(signal['breakdown'])}", flush=True)
    print("", flush=True)


def run_scan():
    print("===================================", flush=True)
    print("🐢 Turtle Trade Daily Scanner", flush=True)
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
        print(f"Leading sector: {SECTOR_MAP.get(top_sector, top_sector)} ({round(sector_returns[top_sector]*100,1)}%)", flush=True)

    new_signals = []
    total_signals = 0
    grade_counts = {"PRIME": 0, "EXCELLENT": 0, "GOOD": 0, "WATCHLIST": 0}

    print(f"Scanning {len(tickers)} tickers...", flush=True)

    for ticker in tickers:
        try:
            signals = scan_ticker(ticker, spy_12m_return, sector_returns)
            for signal in signals:
                total_signals += 1
                key = signal_key(signal)
                if key not in history:
                    history[key] = signal
                    new_signals.append(signal)
                    print_signal(signal)
                    grade = signal.get("grade")
                    if grade in grade_counts:
                        grade_counts[grade] += 1
            time.sleep(0.15)
        except Exception as error:
            print(f"Error scanning {ticker}: {error}", flush=True)

    save_signal_history(history)

    print("===================================", flush=True)
    print(f"Stocks scanned:        {len(tickers)}", flush=True)
    print(f"Total signals today:   {total_signals}", flush=True)
    print(f"New signals recorded:  {len(new_signals)}", flush=True)
    print("-----------------------------------", flush=True)
    print(f"💎 PRIME:              {grade_counts['PRIME']}", flush=True)
    print(f"⭐ EXCELLENT:          {grade_counts['EXCELLENT']}", flush=True)
    print(f"✅ GOOD:               {grade_counts['GOOD']}", flush=True)
    print(f"👀 WATCHLIST:          {grade_counts['WATCHLIST']}", flush=True)
    print("===================================", flush=True)


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
    print(f"Sleeping until next scan in {round(sleep_seconds / 3600, 2)} hours...", flush=True)
    time.sleep(sleep_seconds)
    run_scan()
