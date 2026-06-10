import json
import os
import time
from datetime import datetime, timezone, timedelta
from io import StringIO

import pandas as pd
import requests
import yfinance as yf


SIGNAL_HISTORY_FILE = "signal_history.json"
PAPER_TRADES_FILE = "paper_trades.json"
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


def load_json_file(path, fallback):
    if not os.path.exists(path):
        return fallback

    try:
        with open(path, "r") as file:
            return json.load(file)
    except Exception:
        return fallback


def save_json_file(path, data):
    with open(path, "w") as file:
        json.dump(data, file, indent=2)


def get_market_tickers():
    tickers = set(EXTRA_ETFS)

    try:
        sp500 = fetch_html_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        tickers.update(sp500["Symbol"].tolist())
        print(f"Loaded S&P 500: {len(sp500)} symbols", flush=True)
    except Exception as error:
        print(f"Could not load S&P 500 list: {error}", flush=True)

    try:
        tables = fetch_html_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
        for table in tables:
            if "Ticker" in table.columns:
                tickers.update(table["Ticker"].tolist())
                print(f"Loaded Nasdaq 100: {len(table)} symbols", flush=True)
                break
    except Exception as error:
        print(f"Could not load Nasdaq 100 list: {error}", flush=True)

    clean = sorted([
        str(ticker).replace(".", "-").strip()
        for ticker in tickers
        if isinstance(ticker, str) and ticker.strip()
    ])

    if len(clean) > len(EXTRA_ETFS):
        save_json_file(UNIVERSE_CACHE_FILE, {
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "tickers": clean
        })
        return clean

    cached = load_json_file(UNIVERSE_CACHE_FILE, {})
    if cached.get("tickers"):
        print(f"Using cached universe: {len(cached['tickers'])} symbols", flush=True)
        return cached["tickers"]

    return sorted(EXTRA_ETFS)


def get_spy_12m_return():
    try:
        spy = yf.download(
            "SPY",
            period="13mo",
            interval="1d",
            progress=False,
            auto_adjust=True
        )

        if spy.empty:
            return None

        closes = spy["Close"].squeeze()

        if len(closes) < 252:
            return None

        start = float(closes.iloc[-252])
        end = float(closes.iloc[-1])

        if start <= 0:
            return None

        return (end - start) / start

    except Exception as error:
        print(f"Could not calculate SPY return: {error}", flush=True)
        return None


def get_price_data(ticker):
    return yf.download(
        ticker,
        period="2y",
        interval="1d",
        progress=False,
        auto_adjust=True
    )


def check_prime_turtle_signal(ticker, spy_12m_return, diagnostics, near_prime_candidates):
    data = get_price_data(ticker)

    if data.empty or len(data) < 252:
        return None

    closes = data["Close"].squeeze()
    highs = data["High"].squeeze()
    lows = data["Low"].squeeze()
    volumes = data["Volume"].squeeze()

    close = float(closes.iloc[-1])
    candle_date = data.index[-1].strftime("%Y-%m-%d")

    high_55 = float(highs.iloc[-56:-1].max())
    high_52w = float(highs.iloc[-252:].max())

    ma_200 = float(closes.iloc[-200:].mean())
    ma_50 = float(closes.iloc[-50:].mean())

    avg_volume_20 = float(volumes.iloc[-20:].mean())
    today_volume = float(volumes.iloc[-1])

    # 1. Turtle System 2 entry: 55-day breakout
    if close <= high_55:
        return None

    diagnostics["55_day_breakout"] += 1

    # 2. Long-term trend confirmation
    if close <= ma_200:
        return None

    diagnostics["above_200_ma"] += 1

    # 3. Medium-term trend confirmation
    if close <= ma_50:
        return None

    diagnostics["above_50_ma"] += 1

    # 4. Near or at 52-week high
    if close < high_52w * 0.99:
        return None

    diagnostics["near_52_week_high"] += 1

    # 5. Relative strength versus S&P 500
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

    # 6. Volume confirmation
    if avg_volume_20 <= 0:
        return None

    volume_ratio = today_volume / avg_volume_20
    rounded_volume_ratio = round(volume_ratio, 2)

    if volume_ratio < 1.5:
        near_prime_candidates.append({
            "ticker": ticker,
            "volume_ratio": rounded_volume_ratio,
            "daily_close": round(close, 2),
            "breakout_level": round(high_55, 2),
            "rs_vs_spy": round(rs_diff * 100, 1),
            "candle_date": candle_date,
        })
        return None

    diagnostics["volume_above_1_5"] += 1

    return {
        "ticker": ticker,
        "type": "PRIME_TURTLE_BUY",
        "rule": "System 2 55-day breakout with Prime filters",
        "entry_date": candle_date,
        "entry_price": round(close, 2),
        "daily_close": round(close, 2),
        "breakout_level": round(high_55, 2),
        "ma_200": round(ma_200, 2),
        "ma_50": round(ma_50, 2),
        "high_52w": round(high_52w, 2),
        "rs_vs_spy": round(rs_diff * 100, 1),
        "volume_ratio": rounded_volume_ratio,
    }


def signal_key(signal):
    return f"{signal['entry_date']}|{signal['ticker']}|{signal['type']}"


def create_paper_trade(signal, paper_trades):
    for trade in paper_trades:
        if trade["ticker"] == signal["ticker"] and trade["status"] == "OPEN":
            return False

    paper_trades.append({
        "ticker": signal["ticker"],
        "status": "OPEN",
        "entry_date": signal["entry_date"],
        "entry_price": signal["entry_price"],
        "latest_price": signal["entry_price"],
        "latest_date": signal["entry_date"],
        "return_percent": 0.0,
        "days_open": 0,
        "signal": signal
    })

    return True


def update_open_paper_trades(paper_trades):
    print("Updating open paper trades...", flush=True)

    for trade in paper_trades:
        if trade["status"] != "OPEN":
            continue

        try:
            data = get_price_data(trade["ticker"])

            if data.empty or len(data) < 25:
                continue

            closes = data["Close"].squeeze()
            lows = data["Low"].squeeze()

            latest_price = float(closes.iloc[-1])
            latest_date = data.index[-1].strftime("%Y-%m-%d")

            entry_price = float(trade["entry_price"])
            return_percent = ((latest_price - entry_price) / entry_price) * 100

            entry_dt = datetime.strptime(trade["entry_date"], "%Y-%m-%d")
            latest_dt = datetime.strptime(latest_date, "%Y-%m-%d")
            days_open = (latest_dt - entry_dt).days

            trade["latest_price"] = round(latest_price, 2)
            trade["latest_date"] = latest_date
            trade["return_percent"] = round(return_percent, 2)
            trade["days_open"] = days_open

            # Turtle System 2 exit: 20-day low breakout
            low_20 = float(lows.iloc[-21:-1].min())

            if latest_price < low_20:
                trade["status"] = "CLOSED"
                trade["exit_date"] = latest_date
                trade["exit_price"] = round(latest_price, 2)
                trade["exit_reason"] = "20-day low Turtle exit"
                trade["final_return_percent"] = round(return_percent, 2)

                print("", flush=True)
                print("🔴 🐢 PAPER TRADE CLOSED", flush=True)
                print(f"Ticker:      {trade['ticker']}", flush=True)
                print(f"Entry:       {trade['entry_price']}", flush=True)
                print(f"Exit:        {trade['exit_price']}", flush=True)
                print(f"Return:      {trade['final_return_percent']}%", flush=True)
                print(f"Days held:   {trade['days_open']}", flush=True)
                print("", flush=True)

            time.sleep(0.1)

        except Exception as error:
            print(f"Error updating paper trade {trade['ticker']}: {error}", flush=True)


def print_prime_signal(signal):
    print("", flush=True)
    print("🚨 🐢 PRIME TURTLE SIGNAL", flush=True)
    print(f"Ticker:          {signal['ticker']}", flush=True)
    print(f"Entry Price:     {signal['entry_price']}", flush=True)
    print(f"55-Day Level:    {signal['breakout_level']}", flush=True)
    print(f"200 MA:          {signal['ma_200']}", flush=True)
    print(f"50 MA:           {signal['ma_50']}", flush=True)
    print(f"52-Week High:    {signal['high_52w']}", flush=True)
    print(f"RS vs SPY:       +{signal['rs_vs_spy']}%", flush=True)
    print(f"Volume:          {signal['volume_ratio']}x 20-day avg", flush=True)
    print(f"Entry Date:      {signal['entry_date']}", flush=True)
    print("", flush=True)


def print_near_prime_candidates(near_prime_candidates):
    print("===================================", flush=True)
    print("🚧 NEAR PRIME CANDIDATES", flush=True)
    print("===================================", flush=True)

    if not near_prime_candidates:
        print("No near-prime candidates.", flush=True)
        print("===================================", flush=True)
        return

    near_prime_candidates.sort(
        key=lambda candidate: candidate["volume_ratio"],
        reverse=True
    )

    for candidate in near_prime_candidates[:5]:
        print("", flush=True)
        print(candidate["ticker"], flush=True)
        print("✓ 55-day breakout", flush=True)
        print("✓ Above 200 MA", flush=True)
        print("✓ Above 50 MA", flush=True)
        print("✓ Near 52-week high", flush=True)
        print("✓ RS > 15%", flush=True)
        print(
            f"✗ Volume {candidate['volume_ratio']}x "
            f"(need 1.50x)",
            flush=True
        )

    print("", flush=True)
    print("===================================", flush=True)


def print_paper_trade_summary(paper_trades):
    open_trades = [trade for trade in paper_trades if trade["status"] == "OPEN"]
    closed_trades = [trade for trade in paper_trades if trade["status"] == "CLOSED"]

    print("===================================", flush=True)
    print("📊 PRIME TURTLE PAPER TRADES", flush=True)
    print(f"Open trades:       {len(open_trades)}", flush=True)
    print(f"Closed trades:     {len(closed_trades)}", flush=True)

    if open_trades:
        avg_open_return = sum(trade.get("return_percent", 0) for trade in open_trades) / len(open_trades)
        print(f"Avg open return:   {round(avg_open_return, 2)}%", flush=True)

    if closed_trades:
        winners = [
            trade for trade in closed_trades
            if trade.get("final_return_percent", 0) > 0
        ]
        win_rate = (len(winners) / len(closed_trades)) * 100
        avg_closed_return = sum(
            trade.get("final_return_percent", 0)
            for trade in closed_trades
        ) / len(closed_trades)

        print(f"Win rate:          {round(win_rate, 1)}%", flush=True)
        print(f"Avg closed return: {round(avg_closed_return, 2)}%", flush=True)

    if open_trades:
        print("-----------------------------------", flush=True)
        print("Open positions:", flush=True)

        for trade in open_trades:
            print(
                f"{trade['ticker']} | Entry {trade['entry_price']} | "
                f"Latest {trade['latest_price']} | "
                f"Return {trade['return_percent']}% | "
                f"Days {trade['days_open']}",
                flush=True
            )

    print("===================================", flush=True)


def is_weekend():
    return datetime.now(timezone.utc).weekday() >= 5


def run_scan():
    print("===================================", flush=True)
    print("🐢 Turtle Trade PRIME + Paper Scanner", flush=True)
    print(f"Run time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", flush=True)
    print("===================================", flush=True)

    if is_weekend():
        print("Weekend detected - skipping scan.", flush=True)
        return

    history = load_json_file(SIGNAL_HISTORY_FILE, {})
    paper_trades = load_json_file(PAPER_TRADES_FILE, [])

    update_open_paper_trades(paper_trades)

    tickers = get_market_tickers()

    print("Fetching SPY 12-month return...", flush=True)
    spy_12m_return = get_spy_12m_return()

    if spy_12m_return is not None:
        print(f"SPY 12m return: {round(spy_12m_return * 100, 1)}%", flush=True)

    diagnostics = {
        "55_day_breakout": 0,
        "above_200_ma": 0,
        "above_50_ma": 0,
        "near_52_week_high": 0,
        "rs_above_15": 0,
        "volume_above_1_5": 0,
    }

    near_prime_candidates = []
    prime_signals = []
    new_prime_signals = []
    new_paper_trades = 0

    print(f"Scanning {len(tickers)} tickers for PRIME signals...", flush=True)

    for ticker in tickers:
        try:
            signal = check_prime_turtle_signal(
                ticker,
                spy_12m_return,
                diagnostics,
                near_prime_candidates
            )

            if signal is None:
                time.sleep(0.1)
                continue

            prime_signals.append(signal)
            key = signal_key(signal)

            if key not in history:
                history[key] = signal
                new_prime_signals.append(signal)
                print_prime_signal(signal)

                if create_paper_trade(signal, paper_trades):
                    new_paper_trades += 1

            time.sleep(0.1)

        except Exception as error:
            print(f"Error scanning {ticker}: {error}", flush=True)

    save_json_file(SIGNAL_HISTORY_FILE, history)
    save_json_file(PAPER_TRADES_FILE, paper_trades)

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
    print(f"New paper trades:        {new_paper_trades}", flush=True)
    print("===================================", flush=True)

    if len(prime_signals) == 0:
        print("No PRIME Turtle signals found this scan.", flush=True)

    print_near_prime_candidates(near_prime_candidates)
    print_paper_trade_summary(paper_trades)


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


print("🐢 Turtle Trade Scanner Started - PRIME PAPER MODE", flush=True)

run_scan()

while True:
    sleep_seconds = seconds_until_next_scan()
    print(f"Sleeping until next scan in {round(sleep_seconds / 3600, 2)} hours...", flush=True)
    time.sleep(sleep_seconds)
    run_scan()
