import asyncio
import json
import time
from pathlib import Path

import websockets

# ==============================
# PUMP HUNTER v5
# DATA COLLECTION MODE
# ==============================

PUMP_WS = "wss://pumpportal.fun/api/data"
TRADES_FILE = Path("pump_trades.json")

PAPER_BUY_USD = 100
SOL_USD_ESTIMATE = 150

# Aggressive early entry rules
MIN_ENTRY_AGE = 15
MAX_ENTRY_AGE = 30
MIN_ENTRY_MARKET_CAP = 3_000
MAX_ENTRY_MARKET_CAP = 15_000

# Trade management
STOP_LOSS = -30
TAKE_INITIAL_AT = 100
MOONBAG_TRAIL = 35

MAX_OPEN_TRADES = 10
MAX_TRACKED_TOKENS = 500

EVALUATE_EVERY_SECONDS = 3

tokens = {}
open_trades = {}
already_traded = set()


# ==============================
# HELPERS
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


def percent(value):
    return f"{value:.2f}%"


def load_history():
    if TRADES_FILE.exists():
        try:
            data = json.loads(TRADES_FILE.read_text())
            if isinstance(data, dict):
                data.setdefault("closed", [])
                data.setdefault("opened", [])
                return data
        except Exception:
            pass

    return {"opened": [], "closed": []}


def save_history():
    TRADES_FILE.write_text(json.dumps(trade_history, indent=2))


trade_history = load_history()


# ==============================
# PUMPPORTAL DATA
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

    # For paper testing we can track percentage movement using market cap.
    return market_cap


def get_name(data):
    return data.get("name") or data.get("tokenName") or "Unknown"


def get_symbol(data):
    return data.get("symbol") or data.get("ticker") or "UNKNOWN"


# ==============================
# TOKEN TRACKING
# ==============================

def prune_tokens():
    if len(tokens) <= MAX_TRACKED_TOKENS:
        return

    sorted_tokens = sorted(tokens.values(), key=lambda t: t["created_at"])

    for token in sorted_tokens[:100]:
        mint = token["mint"]

        if mint not in open_trades:
            tokens.pop(mint, None)


def create_or_update_token(data):
    mint = data.get("mint")
    if not mint:
        return None

    market_cap = get_market_cap_usd(data)
    price = get_price(data, market_cap)
    tx_type = data.get("txType")

    if mint not in tokens:
        tokens[mint] = {
            "mint": mint,
            "name": get_name(data),
            "symbol": get_symbol(data),
            "created_at": now(),
            "market_cap": market_cap,
            "last_price": price,
            "highest_market_cap": market_cap,
            "lowest_market_cap": market_cap if market_cap > 0 else None,
            "buys": 0,
            "sells": 0,
            "buy_streak": 0,
            "sell_streak": 0,
            "subscribed": False,
        }

        print(f"👀 Tracking: {tokens[mint]['name']} ({tokens[mint]['symbol']}) | {mint}")

        prune_tokens()

    token = tokens[mint]

    if market_cap > 0:
        token["market_cap"] = market_cap
        token["highest_market_cap"] = max(token.get("highest_market_cap", 0), market_cap)

        if token.get("lowest_market_cap") is None:
            token["lowest_market_cap"] = market_cap
        else:
            token["lowest_market_cap"] = min(token["lowest_market_cap"], market_cap)

    if price > 0:
        token["last_price"] = price

    if tx_type == "buy":
        token["buys"] += 1
        token["buy_streak"] += 1
        token["sell_streak"] = 0

    elif tx_type == "sell":
        token["sells"] += 1
        token["sell_streak"] += 1
        token["buy_streak"] = 0

    return token


# ==============================
# PAPER TRADING
# ==============================

def paper_buy(token):
    mint = token["mint"]

    if mint in open_trades:
        return

    if mint in already_traded:
        return

    if len(open_trades) >= MAX_OPEN_TRADES:
        return

    entry_price = token.get("last_price") or token.get("market_cap")

    if entry_price <= 0:
        return

    trade = {
        "name": token["name"],
        "symbol": token["symbol"],
        "mint": mint,
        "entry_time": now(),
        "entry_age": now() - token["created_at"],
        "entry_price": entry_price,
        "entry_market_cap": token["market_cap"],
        "paper_buy_usd": PAPER_BUY_USD,
        "position_percent": 100,
        "initial_taken": False,
        "highest_pnl": 0,
        "lowest_pnl": 0,
        "status": "OPEN",
        "buys_at_entry": token["buys"],
        "sells_at_entry": token["sells"],
    }

    open_trades[mint] = trade
    already_traded.add(mint)

    trade_history.setdefault("opened", []).append(trade.copy())
    save_history()

    print("\n")
    print("########################################")
    print("🟢 PAPER BUY OPENED")
    print("########################################")
    print(f"Token: {token['name']} ({token['symbol']})")
    print(f"Mint: {mint}")
    print(f"Age: {trade['entry_age']}s")
    print(f"Entry Market Cap: {money(token['market_cap'])}")
    print(f"Buys/Sells at Entry: {token['buys']} / {token['sells']}")
    print(f"Paper Size: ${PAPER_BUY_USD}")
    print("Rule: sell 50% at +100%, moonbag rides")
    print("########################################")
    print("\n")


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

    print("\n")
    print("########################################")
    print("🔴 PAPER TRADE CLOSED")
    print("########################################")
    print(f"Token: {trade['name']} ({trade['symbol']})")
    print(f"Reason: {reason}")
    print(f"P&L: {percent(pnl)}")
    print("########################################")
    print("\n")


def update_open_trade(token):
    mint = token["mint"]

    trade = open_trades.get(mint)
    if not trade:
        return

    entry = safe_float(trade["entry_price"], 0)
    current = safe_float(token.get("last_price"), 0)

    if entry <= 0 or current <= 0:
        return

    pnl = ((current - entry) / entry) * 100

    trade["highest_pnl"] = max(trade.get("highest_pnl", 0), pnl)
    trade["lowest_pnl"] = min(trade.get("lowest_pnl", 0), pnl)

    if pnl <= STOP_LOSS:
        close_trade(mint, "STOP LOSS", pnl)
        return

    if pnl >= TAKE_INITIAL_AT and not trade.get("initial_taken"):
        trade["initial_taken"] = True
        trade["position_percent"] = 50

        print("\n")
        print("########################################")
        print("✅ INITIAL CAPITAL RECOVERED")
        print("########################################")
        print(f"Token: {trade['name']} ({trade['symbol']})")
        print(f"P&L: {percent(pnl)}")
        print("Sold 50% paper position. Remaining 50% is moonbag.")
        print("########################################")
        print("\n")

    if trade.get("initial_taken"):
        trailing_stop = trade["highest_pnl"] - MOONBAG_TRAIL

        if pnl <= trailing_stop:
            close_trade(mint, "TRAILING STOP AFTER MOONBAG", pnl)


# ==============================
# EVALUATION LOOP
# ==============================

def qualifies_for_entry(token):
    age = now() - token["created_at"]
    market_cap = token.get("market_cap", 0)

    if token["mint"] in already_traded:
        return False

    if token["mint"] in open_trades:
        return False

    if len(open_trades) >= MAX_OPEN_TRADES:
        return False

    if age < MIN_ENTRY_AGE:
        return False

    if age > MAX_ENTRY_AGE:
        return False

    if market_cap < MIN_ENTRY_MARKET_CAP:
        return False

    if market_cap > MAX_ENTRY_MARKET_CAP:
        return False

    return True


async def evaluator_loop():
    while True:
        await asyncio.sleep(EVALUATE_EVERY_SECONDS)

        if not tokens:
            continue

        for token in list(tokens.values()):
            update_open_trade(token)

            if qualifies_for_entry(token):
                paper_buy(token)

        top = sorted(
            tokens.values(),
            key=lambda t: t.get("market_cap", 0),
            reverse=True
        )[:5]

        print("📊 Top tracked:")
        for token in top:
            age = now() - token["created_at"]
            print(
                f"{token['symbol']} | "
                f"MC {money(token['market_cap'])} | "
                f"Age {age}s | "
                f"B/S {token['buys']}/{token['sells']}"
            )


# ==============================
# WEBSOCKET
# ==============================

async def handle_message(message, ws):
    try:
        data = json.loads(message)
    except Exception:
        return

    token = create_or_update_token(data)

    if not token:
        return

    if not token["subscribed"]:
        token["subscribed"] = True

        try:
            await ws.send(json.dumps({
                "method": "subscribeTokenTrade",
                "keys": [token["mint"]]
            }))
        except Exception as e:
            print(f"Subscribe error: {e}")


async def main():
    print("========================================")
    print("🚀 PUMP HUNTER v5 STARTED")
    print("========================================")
    print("DATA COLLECTION MODE")
    print("Paper trading only. No real buying.")
    print("")
    print("Entry Rules:")
    print(f"- Age: {MIN_ENTRY_AGE}s to {MAX_ENTRY_AGE}s")
    print(f"- Market Cap: {money(MIN_ENTRY_MARKET_CAP)} to {money(MAX_ENTRY_MARKET_CAP)}")
    print(f"- Max Open Trades: {MAX_OPEN_TRADES}")
    print("")
    print("Exit Rules:")
    print(f"- Stop Loss: {STOP_LOSS}%")
    print(f"- Sell 50% at: +{TAKE_INITIAL_AT}%")
    print(f"- Moonbag Trail: {MOONBAG_TRAIL}%")
    print("========================================\n")

    asyncio.create_task(evaluator_loop())

    while True:
        try:
            async with websockets.connect(
                PUMP_WS,
                ping_interval=20,
                ping_timeout=20
            ) as ws:
                await ws.send(json.dumps({
                    "method": "subscribeNewToken"
                }))

                print("✅ Connected to PumpPortal")
                print("Listening for new pump.fun launches...\n")

                async for message in ws:
                    await handle_message(message, ws)

        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 10 seconds...\n")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
