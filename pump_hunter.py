import asyncio
import json
import time
from pathlib import Path

import websockets

PUMP_WS = "wss://pumpportal.fun/api/data"
TRADES_FILE = Path("pump_trades.json")

PAPER_BUY_USD = 100
ENTRY_SCORE = 80
MIN_AGE_SECONDS = 60
MAX_AGE_SECONDS = 420
MIN_MARKET_CAP = 8_000
MAX_MARKET_CAP = 250_000
STOP_LOSS = -25
TAKE_INITIAL_AT = 100
MOONBAG_TRAIL = 30
MAX_OPEN_TRADES = 5

PRINT_WATCHLIST_ONLY = True

tokens = {}
open_trades = {}


def now():
    return int(time.time())


def load_history():
    if TRADES_FILE.exists():
        try:
            data = json.loads(TRADES_FILE.read_text())
            if isinstance(data, dict):
                data.setdefault("closed", [])
                return data
        except Exception:
            pass
    return {"closed": []}


def save_history():
    TRADES_FILE.write_text(json.dumps({"closed": trade_history["closed"]}, indent=2))


trade_history = load_history()


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def format_money(value):
    return f"${value:,.0f}"


def get_market_cap_usd(data):
    # PumpPortal often gives marketCapSol rather than USD.
    # We use 150 as a rough SOL/USD estimate for v2 paper testing.
    mc_usd = safe_float(data.get("marketCapUsd"), 0)
    if mc_usd > 0:
        return mc_usd

    mc_sol = safe_float(data.get("marketCapSol"), 0)
    if mc_sol > 0:
        return mc_sol * 150

    return safe_float(data.get("marketCap"), 0)


def get_price(data, market_cap):
    price = safe_float(data.get("priceUsd"), 0)
    if price > 0:
        return price

    price = safe_float(data.get("price"), 0)
    if price > 0:
        return price

    # Fallback: use market cap as the tracking value.
    # For paper trading, percentage move still works if we compare like-for-like.
    return market_cap


def calculate_score(token):
    age = now() - token["created_at"]
    market_cap = token.get("market_cap", 0)
    buys = token.get("buys", 0)
    sells = token.get("sells", 0)
    trade_count = buys + sells

    score = 0
    reasons = []

    if MIN_AGE_SECONDS <= age <= 180:
        score += 25
        reasons.append("ideal early age")
    elif 181 <= age <= MAX_AGE_SECONDS:
        score += 15
        reasons.append("still early")
    elif age < MIN_AGE_SECONDS:
        score += 5
        reasons.append("too new, waiting")

    if trade_count >= 40:
        score += 20
        reasons.append("strong trade count")
    elif trade_count >= 20:
        score += 15
        reasons.append("good trade count")
    elif trade_count >= 10:
        score += 8
        reasons.append("early trades forming")

    if sells == 0 and buys >= 10:
        score += 25
        reasons.append("no sells yet")
    elif sells > 0:
        ratio = buys / sells
        if ratio >= 4:
            score += 25
            reasons.append("excellent buy/sell ratio")
        elif ratio >= 2:
            score += 18
            reasons.append("good buy/sell ratio")
        elif ratio >= 1.3:
            score += 8
            reasons.append("weak but positive buy pressure")

    if MIN_MARKET_CAP <= market_cap <= 75_000:
        score += 20
        reasons.append("low early market cap")
    elif 75_001 <= market_cap <= MAX_MARKET_CAP:
        score += 12
        reasons.append("acceptable market cap")

    if not token.get("creator_sold", False):
        score += 10
        reasons.append("no creator sell detected")

    if token.get("recent_buy_streak", 0) >= 5:
        score += 10
        reasons.append("buy streak")

    return min(score, 100), reasons


def print_candidate(token, level):
    age = now() - token["created_at"]
    print("\n===================================")
    print(f"🚀 PUMP HUNTER {level}")
    print("===================================")
    print(f"Token: {token['name']} ({token['symbol']})")
    print(f"Mint: {token['mint']}")
    print(f"Age: {age}s")
    print(f"Score: {token['score']}")
    print(f"Market cap: {format_money(token['market_cap'])}")
    print(f"Buys/Sells: {token['buys']} / {token['sells']}")
    print(f"Reason: {', '.join(token.get('reasons', [])[:4])}")
    print("")


def print_stats():
    closed = trade_history.get("closed", [])
    total_closed = len(closed)
    total_open = len(open_trades)

    if total_closed == 0:
        print(f"📊 Stats | Open: {total_open} | Closed: 0")
        return

    wins = [t for t in closed if t.get("final_pnl", 0) > 0]
    losses = [t for t in closed if t.get("final_pnl", 0) <= 0]

    win_rate = (len(wins) / total_closed) * 100 if total_closed else 0
    largest_winner = max([t.get("final_pnl", 0) for t in closed], default=0)
    largest_loser = min([t.get("final_pnl", 0) for t in closed], default=0)

    avg_win = sum(t.get("final_pnl", 0) for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t.get("final_pnl", 0) for t in losses) / len(losses) if losses else 0

    print(
        f"📊 Stats | Open: {total_open} | Closed: {total_closed} | "
        f"Win rate: {win_rate:.1f}% | Avg win: {avg_win:.1f}% | "
        f"Avg loss: {avg_loss:.1f}% | Best: {largest_winner:.1f}% | Worst: {largest_loser:.1f}%"
    )


def paper_buy(token, price):
    if len(open_trades) >= MAX_OPEN_TRADES:
        return

    mint = token["mint"]
    if mint in open_trades:
        return

    open_trades[mint] = {
        "name": token["name"],
        "symbol": token["symbol"],
        "mint": mint,
        "entry_price": price,
        "entry_market_cap": token.get("market_cap", 0),
        "entry_time": now(),
        "paper_buy_usd": PAPER_BUY_USD,
        "position_percent": 100,
        "initial_taken": False,
        "highest_pnl": 0,
        "status": "OPEN",
    }

    print("\n===================================")
    print("🟢 PAPER BUY OPENED")
    print("===================================")
    print(f"Token: {token['name']} ({token['symbol']})")
    print(f"Mint: {mint}")
    print(f"Score: {token['score']}")
    print(f"Entry tracking price: {price}")
    print(f"Entry market cap: {format_money(token.get('market_cap', 0))}")
    print(f"Paper buy: ${PAPER_BUY_USD}")
    print("Plan: sell 50% at +100%, let rest ride")
    print("")


def close_trade(mint, reason, pnl):
    trade = open_trades.pop(mint, None)
    if not trade:
        return

    trade["exit_time"] = now()
    trade["exit_reason"] = reason
    trade["final_pnl"] = round(pnl, 2)
    trade["status"] = "CLOSED"

    trade_history.setdefault("closed", []).append(trade)
    save_history()

    print("\n===================================")
    print("🔴 PAPER TRADE CLOSED")
    print("===================================")
    print(f"Token: {trade['name']} ({trade['symbol']})")
    print(f"Reason: {reason}")
    print(f"Final P&L: {pnl:.2f}%")
    print("")

    print_stats()


def update_open_trade(mint, current_price):
    trade = open_trades.get(mint)
    if not trade:
        return

    entry = safe_float(trade.get("entry_price"), 0)
    current = safe_float(current_price, 0)

    if entry <= 0 or current <= 0:
        return

    pnl = ((current - entry) / entry) * 100
    trade["highest_pnl"] = max(trade.get("highest_pnl", 0), pnl)

    if pnl <= STOP_LOSS:
        close_trade(mint, "STOP LOSS", pnl)
        return

    if pnl >= TAKE_INITIAL_AT and not trade.get("initial_taken"):
        trade["initial_taken"] = True
        trade["position_percent"] = 50

        print("\n===================================")
        print("✅ INITIAL CAPITAL RECOVERED")
        print("===================================")
        print(f"Token: {trade['name']} ({trade['symbol']})")
        print(f"P&L: {pnl:.2f}%")
        print("Sold 50% paper position. Moonbag now riding.")
        print("")

    if trade.get("initial_taken"):
        trailing_stop = trade["highest_pnl"] - MOONBAG_TRAIL
        if pnl <= trailing_stop:
            close_trade(mint, "TRAILING STOP AFTER MOONBAG", pnl)


def update_token_from_trade(token, tx_type, market_cap, price):
    previous_tx = token.get("last_tx")

    if tx_type == "buy":
        token["buys"] += 1
        token["recent_buy_streak"] = token.get("recent_buy_streak", 0) + 1
    elif tx_type == "sell":
        token["sells"] += 1
        token["recent_buy_streak"] = 0

    if previous_tx == "sell" and tx_type == "buy":
        token["recovery_buys"] = token.get("recovery_buys", 0) + 1

    token["last_tx"] = tx_type

    if market_cap > 0:
        token["market_cap"] = market_cap

    if price > 0:
        token["last_price"] = price

    score, reasons = calculate_score(token)
    token["score"] = score
    token["reasons"] = reasons


async def handle_message(message):
    try:
        data = json.loads(message)
    except Exception:
        return

    mint = data.get("mint")
    if not mint:
        return

    tx_type = data.get("txType")
    name = data.get("name") or data.get("tokenName") or "Unknown"
    symbol = data.get("symbol") or data.get("ticker") or "UNKNOWN"

    market_cap = get_market_cap_usd(data)
    price = get_price(data, market_cap)

    if mint not in tokens:
        tokens[mint] = {
            "mint": mint,
            "name": name,
            "symbol": symbol,
            "created_at": now(),
            "buys": 0,
            "sells": 0,
            "market_cap": market_cap,
            "creator_sold": False,
            "score": 0,
            "reasons": [],
            "recent_buy_streak": 0,
            "last_price": price,
            "last_alert_level": None,
        }

        if not PRINT_WATCHLIST_ONLY:
            print(f"🆕 New token: {name} ({symbol}) | {mint}")

    token = tokens[mint]
    update_token_from_trade(token, tx_type, market_cap, price)

    if mint in open_trades:
        update_open_trade(mint, price)

    age = now() - token["created_at"]

    if age < MIN_AGE_SECONDS:
        return

    if age > MAX_AGE_SECONDS:
        return

    if token["market_cap"] > MAX_MARKET_CAP:
        return

    if token["score"] >= 90 and token.get("last_alert_level") != "PRIME":
        token["last_alert_level"] = "PRIME"
        print_candidate(token, "PRIME CANDIDATE")

    elif token["score"] >= 80 and token.get("last_alert_level") not in ["STRONG", "PRIME"]:
        token["last_alert_level"] = "STRONG"
        print_candidate(token, "STRONG WATCH")

    elif token["score"] >= 70 and token.get("last_alert_level") is None:
        token["last_alert_level"] = "WATCHLIST"
        print_candidate(token, "WATCHLIST")

    if (
        token["score"] >= ENTRY_SCORE
        and MIN_MARKET_CAP <= token["market_cap"] <= MAX_MARKET_CAP
        and mint not in open_trades
    ):
        paper_buy(token, price)


async def main():
    print("===================================")
    print("🚀 PUMP HUNTER v2 STARTED")
    print("===================================")
    print("Paper trading only. No real buying.")
    print("Scanning live pump.fun launches.")
    print("Only Watchlist / Strong / Prime candidates will print.")
    print("")

    while True:
        try:
            async with websockets.connect(PUMP_WS, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))

                print("✅ Connected to PumpPortal")
                print("Listening for new launches...\n")

                async for message in ws:
                    try:
                        await handle_message(message)
                    except Exception as e:
                        print(f"Message error: {e}")

        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 10 seconds...\n")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
