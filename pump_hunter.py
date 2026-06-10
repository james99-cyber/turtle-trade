import asyncio
import json
import time
from pathlib import Path
import websockets

PUMP_WS = "wss://pumpportal.fun/api/data"
TRADES_FILE = Path("pump_trades.json")

PAPER_BUY_USD = 100
SOL_USD_ESTIMATE = 150

MIN_AGE_SECONDS = 20
MAX_AGE_SECONDS = 600

WATCH_SCORE = 40
ENTRY_SCORE = 55
PRIME_SCORE = 75

MIN_MARKET_CAP = 1_500
MAX_MARKET_CAP = 500_000

STOP_LOSS = -25
TAKE_INITIAL_AT = 100
MOONBAG_TRAIL = 30

MAX_OPEN_TRADES = 5
MAX_TRACKED_TOKENS = 300
EVALUATE_EVERY_SECONDS = 5

tokens = {}
open_trades = {}


def now():
    return int(time.time())


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def money(value):
    return f"${value:,.0f}"


def load_history():
    if TRADES_FILE.exists():
        try:
            data = json.loads(TRADES_FILE.read_text())
            data.setdefault("closed", [])
            return data
        except Exception:
            pass
    return {"closed": []}


def save_history():
    TRADES_FILE.write_text(json.dumps(trade_history, indent=2))


trade_history = load_history()


def get_market_cap_usd(data):
    if safe_float(data.get("marketCapUsd")) > 0:
        return safe_float(data.get("marketCapUsd"))

    if safe_float(data.get("marketCapSol")) > 0:
        return safe_float(data.get("marketCapSol")) * SOL_USD_ESTIMATE

    if safe_float(data.get("marketCap")) > 0:
        return safe_float(data.get("marketCap"))

    return 0


def get_price(data, market_cap):
    if safe_float(data.get("priceUsd")) > 0:
        return safe_float(data.get("priceUsd"))

    if safe_float(data.get("price")) > 0:
        return safe_float(data.get("price"))

    return market_cap


def get_name(data):
    return data.get("name") or data.get("tokenName") or "Unknown"


def get_symbol(data):
    return data.get("symbol") or data.get("ticker") or "UNKNOWN"


def calculate_score(token):
    age = now() - token["created_at"]
    mc = token.get("market_cap", 0)
    buys = token.get("buys", 0)
    sells = token.get("sells", 0)
    trades = buys + sells

    score = 0
    reasons = []

    if 20 <= age <= 180:
        score += 20
        reasons.append("early age")
    elif 181 <= age <= 600:
        score += 10
        reasons.append("still young")

    if MIN_MARKET_CAP <= mc <= 50_000:
        score += 25
        reasons.append("very early market cap")
    elif 50_001 <= mc <= MAX_MARKET_CAP:
        score += 15
        reasons.append("acceptable market cap")

    if trades >= 20:
        score += 20
        reasons.append("trade activity")
    elif trades >= 5:
        score += 10
        reasons.append("some activity")
    else:
        score += 5
        reasons.append("launch detected")

    if sells == 0 and buys >= 3:
        score += 20
        reasons.append("buy pressure no sells")
    elif sells > 0:
        ratio = buys / sells
        if ratio >= 2:
            score += 20
            reasons.append("good buy/sell ratio")
        elif ratio >= 1:
            score += 10
            reasons.append("positive buy pressure")

    if token.get("buy_streak", 0) >= 3:
        score += 10
        reasons.append("buy streak")

    score += 5
    reasons.append("no creator sell detected")

    return min(score, 100), reasons


def print_candidate(token, level):
    print("\n===================================")
    print(f"🚀 PUMP HUNTER {level}")
    print("===================================")
    print(f"Token: {token['name']} ({token['symbol']})")
    print(f"Mint: {token['mint']}")
    print(f"Age: {now() - token['created_at']}s")
    print(f"Score: {token['score']}")
    print(f"Market cap: {money(token['market_cap'])}")
    print(f"Buys/Sells: {token['buys']} / {token['sells']}")
    print(f"Reasons: {', '.join(token['reasons'][:5])}")
    print("===================================\n")


def paper_buy(token):
    mint = token["mint"]

    if mint in open_trades:
        return

    if len(open_trades) >= MAX_OPEN_TRADES:
        return

    price = token.get("last_price") or token.get("market_cap")
    if price <= 0:
        return

    open_trades[mint] = {
        "name": token["name"],
        "symbol": token["symbol"],
        "mint": mint,
        "entry_time": now(),
        "entry_price": price,
        "entry_market_cap": token["market_cap"],
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
    print(f"Entry market cap: {money(token['market_cap'])}")
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

    trade_history["closed"].append(trade)
    save_history()

    print("\n🔴 PAPER TRADE CLOSED")
    print(f"{trade['name']} ({trade['symbol']}) | {reason} | {pnl:.2f}%\n")


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
        print("\n✅ INITIAL CAPITAL RECOVERED")
        print(f"{trade['name']} ({trade['symbol']}) | P&L {pnl:.2f}%")
        print("Sold 50% paper position. Moonbag riding.\n")

    if trade["initial_taken"]:
        trailing_stop = trade["highest_pnl"] - MOONBAG_TRAIL
        if pnl <= trailing_stop:
            close_trade(mint, "TRAILING STOP", pnl)


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
            "buys": 0,
            "sells": 0,
            "buy_streak": 0,
            "sell_streak": 0,
            "score": 0,
            "reasons": [],
            "last_alert": None,
            "subscribed": False,
        }

        print(f"👀 Tracking: {tokens[mint]['name']} ({tokens[mint]['symbol']}) | {mint}")

    token = tokens[mint]

    if market_cap > 0:
        token["market_cap"] = market_cap

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

    score, reasons = calculate_score(token)
    token["score"] = score
    token["reasons"] = reasons

    if mint in open_trades:
        update_open_trade(mint, token["last_price"])

    return token


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


async def evaluator_loop():
    while True:
        await asyncio.sleep(EVALUATE_EVERY_SECONDS)

        candidates = []

        for token in list(tokens.values()):
            age = now() - token["created_at"]

            if age > MAX_AGE_SECONDS and token["mint"] not in open_trades:
                tokens.pop(token["mint"], None)
                continue

            score, reasons = calculate_score(token)
            token["score"] = score
            token["reasons"] = reasons

            if age < MIN_AGE_SECONDS:
                continue

            if token["market_cap"] < MIN_MARKET_CAP:
                continue

            if token["market_cap"] > MAX_MARKET_CAP:
                continue

            candidates.append(token)

            if score >= PRIME_SCORE and token["last_alert"] != "PRIME":
                token["last_alert"] = "PRIME"
                print_candidate(token, "PRIME CANDIDATE")

            elif score >= ENTRY_SCORE and token["last_alert"] not in ["STRONG", "PRIME"]:
                token["last_alert"] = "STRONG"
                print_candidate(token, "STRONG WATCH")

            elif score >= WATCH_SCORE and token["last_alert"] is None:
                token["last_alert"] = "WATCHLIST"
                print_candidate(token, "WATCHLIST")

            if score >= ENTRY_SCORE and token["mint"] not in open_trades:
                paper_buy(token)

        if tokens:
            top = sorted(tokens.values(), key=lambda x: x.get("score", 0), reverse=True)[:3]
            print("📊 Top tracked:")
            for t in top:
                print(
                    f"{t['symbol']} | Score {t['score']} | "
                    f"MC {money(t['market_cap'])} | "
                    f"B/S {t['buys']}/{t['sells']} | "
                    f"Age {now() - t['created_at']}s"
                )


async def main():
    print("===================================")
    print("🚀 PUMP HUNTER v4 STARTED")
    print("===================================")
    print("Paper trading only. No real buying.")
    print("Now includes background evaluator.")
    print(f"Entry score: {ENTRY_SCORE}")
    print(f"Minimum age: {MIN_AGE_SECONDS}s")
    print(f"Market cap: {money(MIN_MARKET_CAP)} - {money(MAX_MARKET_CAP)}")
    print("===================================\n")

    asyncio.create_task(evaluator_loop())

    while True:
        try:
            async with websockets.connect(PUMP_WS, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))

                print("✅ Connected to PumpPortal")
                print("Listening for launches and trades...\n")

                async for message in ws:
                    await handle_message(message, ws)

        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 10 seconds...\n")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
