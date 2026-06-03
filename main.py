import time
from datetime import datetime
import yfinance as yf

TICKERS = [
    "SPY", "QQQ", "GLD", "SLV",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL",
    "TSLA", "AMD", "AVGO", "PLTR", "NFLX", "COST",
    "JPM", "V", "MA", "UNH", "LLY", "XOM"
]

def latest_number(series):
    return float(series.squeeze())

def scan_ticker(ticker):
    data = yf.download(
        ticker,
        period="1y",
        interval="1d",
        progress=False,
        auto_adjust=True
    )

    if data.empty or len(data) < 220:
        return None

    close = latest_number(data["Close"].iloc[-1])
    high_20 = latest_number(data["High"].iloc[-21:-1].max())
    high_55 = latest_number(data["High"].iloc[-56:-1].max())
    low_10 = latest_number(data["Low"].iloc[-11:-1].min())
    ma_200 = latest_number(data["Close"].rolling(200).mean().iloc[-1])

    if close > high_55 and close > ma_200:
        return f"🐢 STRONG BUY: {ticker} | 55-day breakout | Price: {close:.2f}"

    if close > high_20 and close > ma_200:
        return f"🐢 BUY: {ticker} | 20-day breakout | Price: {close:.2f}"

    if close < low_10:
        return f"🔴 SELL: {ticker} | 10-day low broken | Price: {close:.2f}"

    return None

def run_scan():
    print("===================================", flush=True)
    print(f"🐢 Turtle Trade Scan: {datetime.now()}", flush=True)
    print("===================================", flush=True)

    signals = []

    for ticker in TICKERS:
        try:
            signal = scan_ticker(ticker)

            if signal:
                signals.append(signal)
                print(signal, flush=True)
            else:
                print(f"No signal: {ticker}", flush=True)

        except Exception as e:
            print(f"Error scanning {ticker}: {e}", flush=True)

    print("-----------------------------------", flush=True)
    print(f"Scan complete. Signals found: {len(signals)}", flush=True)
    print("-----------------------------------", flush=True)

print("🐢 Turtle Trade Scanner Started - VERSION 3", flush=True)

while True:
    run_scan()
    print("Sleeping for 5 minutes...", flush=True)
    time.sleep(300)
