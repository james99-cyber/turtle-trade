import asyncio
import json
import time
from pathlib import Path

import websockets

# ==============================
# PUMP HUNTER CONFIG
# ==============================

PUMP_WS = "wss://pumpportal.fun/api/data"
TRADES_FILE = Path("pump_trades.json")

PAPER_BUY_USD = 100

ENTRY_SCORE = 80
WATCH_SCORE = 70
PRIME_SCORE = 90

MIN_AGE_SECONDS = 60
MAX_AGE_SECONDS = 420

MIN_MARKET_CAP = 8_000
MAX_MARKET_CAP = 250_000

STOP_LOSS = -25
TAKE_INITIAL_AT = 100
MOONBAG_TRAIL = 30

MAX_OPEN_TRADES = 5
MAX_TRACKED_TOKENS = 200

SOL_USD_ESTIMATE = 150

tokens = {}
open_trades = {}


# ==============================
# BASIC HELPERS
# ==============================

def now():
    return int(time.time())


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def money(value):
    return f"${value:,.0f}"


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
    TRADES_FILE.write_text(json.dumps(trade_history, indent=2))


trade_history = load_history()


# ==============================
# DATA EXTRACTION
# ==============================

def get_market_cap_usd(data):
    market_cap_usd = safe_float(data.get("marketCapUsd"), 0)
    if market_cap_usd > 0:
        return market_cap_usd

    market_cap_sol = safe_float(data.get("marketCapSol"), 0)
    if market_cap_sol > 0:
        return market_cap_sol * SOL_USD_ESTIMATE

    market_cap = safe_float(data.get("marketCap"), 0)
    if market_cap > 0:
        return market_cap

    return 0


def get_price(data, market_cap):
    price_usd = safe_float(data.get("priceUsd"), 0)
    if price_usd > 0:
        return price_usd

    price = safe_float(data.get("price"), 0)
    if price > 0:
        return price

    # Fallback for paper trading:
    # using market cap as a tracking value still gives us % movement.
    return market_cap


def get_token_name(data):
    return (
        data.get("name")
        or data.get("tokenName")
        or data.get("metadata", {}).get("name")
        or "Unknown"
    )


def get_token_symbol(data):
    return (
        data.get("symbol")
        or data.get("ticker")
        or data.get("metadata", {}).get("symbol")
        or "UNKNOWN"
    )


# ==============================
# SCORING ENGINE
# ==============================

def calculate_score(token):
    age = now() - token["created_at"]
    market_cap = token.get("market_cap", 0)
    buys = token.get("buys", 0)
    sells = token.get("sells", 0)
    trade_count = buys + sells

    score = 0
    reasons = []

    # Age scoring
    if MIN_AGE_SECONDS <= age <= 180:
        score += 25
        reasons.append("ideal early age")
    elif 181 <= age <= MAX_AGE_SECONDS:
        score += 15
        reasons.append("still early")
    elif age < MIN_AGE_SECONDS:
        score += 5
        reasons.append("waiting for confirmation")

    # Trade activity
    if trade_count >= 50:
        score += 20
        reasons.append("very strong trade activity")
    elif trade_count >= 30:
        score += 15
        reasons.append("strong trade activity")
    elif trade_count >= 15:
        score += 10
        reasons.append("activity building")

    # Buy pressure
    if sells == 0 and buys >= 10:
        score += 25
        reasons.append("buys with no sells")
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
            reasons.append("positive buy pressure")

    # Market cap
    if MIN_MARKET_CAP <= market_cap <= 75_000:
        score += 20
        reasons.append("low early market cap")
    elif 75_001 <= market_cap <= MAX_MARKET_CAP:
        score += 12
        reasons.append("acceptable market cap")

    # Buy streak
    if token.get("buy_streak", 0) >= 5:
        score += 10
        reasons.append("buy streak")

    # Creator safety placeholder
    if not token.get("creator_sold", False):
        score += 10
        reasons.append("no creator sell detected")

    return min(score, 100), reasons


# ==============================
# PAPER TRADING
# ==============================

def paper_buy(token, price):
    mint = token["mint"]

    if mint in open_trades:
        return

    if len(open_trades) >= MAX_OPEN_TRADES:
        print("⚠️ Max open trades reached. No new paper buy.")
        return

    open_trades[mint] = {
        "name": token["name"],
        "symbol": token["symbol"],
        "mint": mint,
        "entry_time": now(),
        "entry_price": price,
        "entry_market_cap": token.get("market_cap", 0),
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
    print(f"Entry market cap: {money(token.get('market_cap', 0))}")
    print(f"Paper buy: ${PAPER_BUY_USD}")
    print("Plan: sell 50% at +100%, let rest ride")
    print("===================================\n")


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
    print("===================================\n")


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
        print("===================================\n")

    if trade.get("initial_taken"):
        trailing_stop_level = trade["highest_pnl"] - MOONBAG_TRAIL

        if pnl <= trailing_stop_level:
            close_trade(mint, "TRAILING STOP AFTER MOONBAG", pnl)


# ==============================
# LOGGING
# ==============================

def print_candidate(token, level):
    age = now() - token["created_at"]

    print("\n===================================")
    print(f"🚀 PUMP HUNTER {level}")
    print("===================================")
    print(f"Token: {token['name']} ({token['symbol']})")
    print(f"Mint: {token['mint']}")
    print(f"Age: {age}s")
    print(f"Score: {token['score']}")
    print(f"Market cap: {money(token.get('market_cap', 0))}")
    print(f"Buys/Sells: {token.get('buys', 0)} / {token.get('sells', 0)}")
    print(f"Reasons: {', '.join(token.get('reasons', [])[:5])}")
    print("===================================\n")


# ==============================
# TOKEN MANAGEMENT
# ==============================

def prune_tokens():
    if len(tokens) <= MAX_TRACKED_TOKENS:
        return

    old_tokens = sorted(
        tokens.values(),
        key=lambda t: t.get("created_at", 0)
    )

    for token in old_tokens[:50]:
        mint = token["mint"]
        if mint not in open_trades:
            tokens.pop(mint, None)


def create_token(data):
    mint = data.get("mint")

    market_cap = get_market_cap_usd(data)
    price = get_price(data, market_cap)

    token = {
        "mint": mint,
        "name": get_token_name(data),
        "symbol": get_token_symbol(data),
        "created_at": now(),
        "buys": 0,
        "sells": 0,
        "buy_streak": 0,
        "sell_streak": 0,
        "market_cap": market_cap,
        "last_price": price,
        "creator_sold": False,
        "score": 0,
        "reasons": [],
        "last_alert": None,
    }

    tokens[mint] = token
    prune_tokens()

    return token


def update_token(token, data):
    tx_type = data.get("txType")
    market_cap = get_market_cap_usd(data)
    price = get_price(data, market_cap)

    if tx_type == "buy":
        token["buys"] += 1
        token["buy_streak"] += 1
        token["sell_streak"] = 0

    elif tx_type == "sell":
        token["sells"] += 1
        token["sell_streak"] += 1
        token["buy_streak"] = 0

    if market_cap > 0:
        token["market_cap"] = market_cap

    if price > 0:
        token["last_price"] = price

    score, reasons = calculate_score(token)
    token["score"] = score
    token["reasons"] = reasons

    return price


async def subscribe_to_token(ws, mint, token):
    try:
        await ws.send(json.dumps({
            "method": "subscribeTokenTrade",
            "keys": [mint]
        }))

        print(f"👀 Tracking: {token['name']} ({token['symbol']}) | {mint}")

    except Exception as e:
        print(f"Subscription error for {mint}: {e}")


# ==============================
# MESSAGE HANDLER
# ==============================

async def handle_message(message, ws):
    try:
        data = json.loads(message)
    except Exception:
        return

    mint = data.get("mint")
    if not mint:
        return

    if mint not in tokens:
        token = create_token(data)
        await subscribe_to_token(ws, mint, token)
    else:
        token = tokens[mint]

    price = update_token(token, data)

    if mint in open_trades:
        update_open_trade(mint, price)

    age = now() - token["created_at"]

    if age < MIN_AGE_SECONDS:
        return

    if age > MAX_AGE_SECONDS:
        return

    if token["market_cap"] < MIN_MARKET_CAP:
        return

    if token["market_cap"] > MAX_MARKET_CAP:
        return

    if token["score"] >= PRIME_SCORE and token.get("last_alert") != "PRIME":
        token["last_alert"] = "PRIME"
        print_candidate(token, "PRIME CANDIDATE")

    elif token["score"] >= ENTRY_SCORE and token.get("last_alert") not in ["STRONG", "PRIME"]:
        token["last_alert"] = "STRONG"
        print_candidate(token, "STRONG WATCH")

    elif token["score"] >= WATCH_SCORE and token.get("last_alert") is None:
        token["last_alert"] = "WATCHLIST"
        print_candidate(token, "WATCHLIST")

    if (
        token["score"] >= ENTRY_SCORE
        and mint not in open_trades
        and len(open_trades) < MAX_OPEN_TRADES
    ):
        paper_buy(token, price)


# ==============================
# MAIN LOOP
# ==============================

async def main():
    print("===================================")
    print("🚀 PUMP HUNTER v3 STARTED")
    print("===================================")
    print("Paper trading only. No real buying.")
    print("Live pump.fun launch + trade tracking.")
    print("Rules:")
    print(f"- Wait minimum {MIN_AGE_SECONDS}s")
    print(f"- Entry score >= {ENTRY_SCORE}")
    print(f"- Market cap between {money(MIN_MARKET_CAP)} and {money(MAX_MARKET_CAP)}")
    print(f"- Sell 50% at +{TAKE_INITIAL_AT}%")
    print(f"- Stop loss {STOP_LOSS}%")
    print("===================================\n")

    while True:
        try:
            async with websockets.connect(
                PUMP_WS,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:

                await ws.send(json.dumps({
                    "method": "subscribeNewToken"
                }))

                print("✅ Connected to PumpPortal")
                print("Listening for launches and subscribing to trades...\n")

                async for message in ws:
                    try:
                        await handle_message(message, ws)
                    except Exception as e:
                        print(f"Message error: {e}")

        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 10 seconds...\n")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
