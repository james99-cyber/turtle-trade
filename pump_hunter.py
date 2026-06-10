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
MAX_MARKET_CAP = 250_000
STOP_LOSS = -25
TAKE_INITIAL_AT = 100
MAX_OPEN_TRADES = 5


tokens = {}
open_trades = {}


def now():
    return int(time.time())


def load_trades():
    if TRADES_FILE.exists():
        try:
            return json.loads(TRADES_FILE.read_text())
        except Exception:
            return {"closed": []}
    return {"closed": []}


def save_trades(data):
    TRADES_FILE.write_text(json.dumps(data, indent=2))


trade_history = load_trades()


def score_token(token):
    age = now() - token["created_at"]
    market_cap = token.get("market_cap", 0)
    buys = token.get("buys", 0)
    sells = token.get("sells", 0)
    trades = buys + sells

    score = 0

    if 60 <= age <= 300:
        score += 30
    elif age < 60:
        score += 10

    if trades >= 10:
        score += 15

    if buys >= max(1, sells * 2):
        score += 25

    if 10_000 <= market_cap <= MAX_MARKET_CAP:
        score += 20

    if not token.get("creator_sold", False):
        score += 10

    return min(score, 100)


def paper_buy(token, price):
    if len(open_trades) >= MAX_OPEN_TRADES:
        return

    mint = token["mint"]

    if mint in open_trades:
        return

    open_trades[mint] = {
        "name": token.get("name", "Unknown"),
        "symbol": token.get("symbol", "UNKNOWN"),
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
    print("🚀 PUMP HUNTER PAPER BUY")
    print("===================================")
    print(f"Token: {token.get('name')} ({token.get('symbol')})")
    print(f"Mint: {mint}")
    print(f"Score: {token.get('score')}")
    print(f"Entry price: {price}")
    print(f"Market cap: ${token.get('market_cap', 0):,.0f}")
    print(f"Paper buy: ${PAPER_BUY_USD}")
    print("Rule: sell 50% at +100%, let rest ride\n")


def close_trade(mint, reason, pnl):
    trade = open_trades.pop(mint)
    trade["exit_time"] = now()
    trade["exit_reason"] = reason
    trade["final_pnl"] = pnl
    trade["status"] = "CLOSED"

    trade_history.setdefault("closed", []).append(trade)
    save_trades(trade_history)

    print("\n===================================")
    print("📉 PUMP HUNTER TRADE CLOSED")
    print("===================================")
    print(f"Token: {trade['name']} ({trade['symbol']})")
    print(f"Reason: {reason}")
    print(f"Final P&L: {pnl:.2f}%\n")


def update_open_trade(mint, current_price):
    trade = open_trades.get(mint)
    if not trade:
        return

    entry = trade["entry_price"]
    if entry <= 0 or current_price <= 0:
        return

    pnl = ((current_price - entry) / entry) * 100
    trade["highest_pnl"] = max(trade["highest_pnl"], pnl)

    if pnl <= STOP_LOSS:
        close_trade(mint, "STOP LOSS", pnl)
        return

    if pnl >= TAKE_INITIAL_AT and not trade["initial_taken"]:
        trade["initial_taken"] = True
        trade["position_percent"] = 50

        print("\n===================================")
        print("✅ INITIAL CAPITAL RECOVERED")
        print("===================================")
        print(f"Token: {trade['name']} ({trade['symbol']})")
        print(f"P&L: {pnl:.2f}%")
        print("Sold 50% paper position. Moonbag now riding.\n")

    if trade["initial_taken"]:
        trailing_stop_level = trade["highest_pnl"] - 30
        if pnl <= trailing_stop_level:
            close_trade(mint, "TRAILING STOP AFTER MOONBAG", pnl)


async def subscribe(ws):
    await ws.send(json.dumps({"method": "subscribeNewToken"}))
    print("✅ Pump Hunter connected")
    print("Listening for new pump.fun launches...\n")


async def handle_message(message):
    data = json.loads(message)

    mint = data.get("mint")
    if not mint:
        return

    tx_type = data.get("txType")
    name = data.get("name") or data.get("tokenName") or "Unknown"
    symbol = data.get("symbol") or data.get("ticker") or "UNKNOWN"

    market_cap = float(data.get("marketCapSol", 0) or 0) * 150
    price = float(data.get("price", 0) or data.get("priceUsd", 0) or market_cap or 0)

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
        }

        print(f"🆕 New token: {name} ({symbol}) | {mint}")

        async with websockets.connect(PUMP_WS) as trade_ws:
            await trade_ws.send(json.dumps({
                "method": "subscribeTokenTrade",
                "keys": [mint]
            }))

    token = tokens[mint]
    token["market_cap"] = max(token.get("market_cap", 0), market_cap)

    if tx_type == "buy":
        token["buys"] += 1
    elif tx_type == "sell":
        token["sells"] += 1

    token["score"] = score_token(token)
    age = now() - token["created_at"]

    if mint in open_trades:
        update_open_trade(mint, price)

    if (
        age >= MIN_AGE_SECONDS
        and token["score"] >= ENTRY_SCORE
        and token["market_cap"] <= MAX_MARKET_CAP
        and mint not in open_trades
    ):
        paper_buy(token, price)


async def main():
    print("===================================")
    print("🚀 PUMP HUNTER v1 STARTED")
    print("===================================")
    print("Paper trading only. No real buying.\n")

    while True:
        try:
            async with websockets.connect(PUMP_WS, ping_interval=20, ping_timeout=20) as ws:
                await subscribe(ws)

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
