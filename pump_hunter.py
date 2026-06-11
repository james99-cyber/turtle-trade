import asyncio
import json
import time
from pathlib import Path

import websockets

PUMP_WS = "wss://pumpportal.fun/api/data"
TRADES_FILE = Path("pump_trades.json")

STARTING_BALANCE = 1000
PAPER_BUY_USD = 100
SOL_USD_ESTIMATE = 150

MIN_ENTRY_AGE = 15
MAX_ENTRY_AGE = 300
MIN_ENTRY_MARKET_CAP = 5_000
MAX_ENTRY_MARKET_CAP = 75_000
MIN_BUYS_AT_ENTRY = 1

STOP_LOSS = -30
TAKE_INITIAL_AT = 100
MOONBAG_TRAIL = 35

MAX_OPEN_TRADES = 10
MAX_TRACKED_TOKENS = 500

EVALUATE_EVERY_SECONDS = 3
SUMMARY_EVERY_SECONDS = 300
DIAGNOSTIC_EVERY_SECONDS = 60

tokens = {}
open_trades = {}
already_traded = set()
last_summary_time = 0
last_diagnostic_time = 0


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
    return f"${value:,.2f}"


def pct(value):
    return f"{value:.2f}%"


def load_history():
    if TRADES_FILE.exists():
        try:
            data = json.loads(TRADES_FILE.read_text())
            if isinstance(data, dict):
                data.setdefault("opened", [])
                data.setdefault("closed", [])
                return data
        except Exception:
            pass
    return {"opened": [], "closed": []}


def save_history():
    TRADES_FILE.write_text(json.dumps(trade_history, indent=2))


trade_history = load_history()


def get_market_cap_usd(data):
    mc_usd = safe_float(data.get("marketCapUsd"), 0)
    if mc_usd > 0:
        return mc_usd

    mc_sol = safe_float(data.get("marketCapSol"), 0)
    if mc_sol > 0:
        return mc_sol * SOL_USD_ESTIMATE

    mc = safe_float(data.get("marketCap"), 0)
    if mc > 0:
        return mc

    return 0


def get_price(data, market_cap):
    price_usd = safe_float(data.get("priceUsd"), 0)
    if price_usd > 0:
        return price_usd

    price = safe_float(data.get("price"), 0)
    if price > 0:
        return price

    return market_cap


def get_name(data):
    return data.get("name") or data.get("tokenName") or "Unknown"


def get_symbol(data):
    return data.get("symbol") or data.get("ticker") or "UNKNOWN"


def current_balance():
    realised = 0

    for trade in trade_history.get("closed", []):
        pnl = safe_float(trade.get("final_pnl"), 0)
        realised += PAPER_BUY_USD * (pnl / 100)

    return STARTING_BALANCE + realised


def unrealised_pnl_usd():
    total = 0

    for trade in open_trades.values():
        pnl = safe_float(trade.get("current_pnl"), 0)
        size = PAPER_BUY_USD * (safe_float(trade.get("position_percent"), 100) / 100)
        total += size * (pnl / 100)

    return total


def print_account_summary(force=False):
    global last_summary_time

    if not force and now() - last_summary_time < SUMMARY_EVERY_SECONDS:
        return

    last_summary_time = now()

    closed = trade_history.get("closed", [])
    wins = [t for t in closed if safe_float(t.get("final_pnl"), 0) > 0]
    losses = [t for t in closed if safe_float(t.get("final_pnl"), 0) <= 0]

    realised_balance = current_balance()
    unrealised = unrealised_pnl_usd()
    equity = realised_balance + unrealised

    best = max([safe_float(t.get("final_pnl"), 0) for t in closed], default=0)
    worst = min([safe_float(t.get("final_pnl"), 0) for t in closed], default=0)
    win_rate = (len(wins) / len(closed) * 100) if closed else 0

    print("\n========================================")
    print("📊 PUMP HUNTER PAPER ACCOUNT")
    print("========================================")
    print(f"Starting Balance: {money(STARTING_BALANCE)}")
    print(f"Realised Balance: {money(realised_balance)}")
    print(f"Unrealised P&L: {money(unrealised)}")
    print(f"Estimated Equity: {money(equity)}")
    print("")
    print(f"Open Trades: {len(open_trades)}")
    print(f"Closed Trades: {len(closed)}")
    print(f"Wins: {len(wins)}")
    print(f"Losses: {len(losses)}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Best Trade: {pct(best)}")
    print(f"Worst Trade: {pct(worst)}")

    if open_trades:
        print("")
        print("Open Positions:")

        sorted_trades = sorted(
            open_trades.values(),
            key=lambda t: safe_float(t.get("current_pnl"), 0),
            reverse=True
        )

        for trade in sorted_trades:
            print(
                f"- {trade['symbol']} | "
                f"Now {pct(trade.get('current_pnl', 0))} | "
                f"Peak {pct(trade.get('highest_pnl', 0))} | "
                f"Low {pct(trade.get('lowest_pnl', 0))} | "
                f"Size {trade.get('position_percent', 100)}%"
            )

    print("========================================\n")


def print_diagnostics(force=False):
    global last_diagnostic_time

    if not force and now() - last_diagnostic_time < DIAGNOSTIC_EVERY_SECONDS:
        return

    last_diagnostic_time = now()

    total = len(tokens)
    active_window = 0
    mc_window = 0
    buy_window = 0
    full_candidates = 0

    examples = []

    for token in tokens.values():
        age = now() - token["created_at"]
        mc = token.get("market_cap", 0)
        buys = token.get("buys", 0)
        sells = token.get("sells", 0)

        in_age = MIN_ENTRY_AGE <= age <= MAX_ENTRY_AGE
        in_mc = MIN_ENTRY_MARKET_CAP <= mc <= MAX_ENTRY_MARKET_CAP
        has_buys = buys >= MIN_BUYS_AT_ENTRY
        buy_pressure = buys > sells

        if in_age:
            active_window += 1

        if in_age and in_mc:
            mc_window += 1

        if in_age and in_mc and has_buys:
            buy_window += 1

        if in_age and in_mc and has_buys and buy_pressure:
            full_candidates += 1

        if len(examples) < 5 and in_age:
            examples.append(
                f"{token['symbol']} | Age {age}s | MC {money(mc)} | B/S {buys}/{sells}"
            )

    print("\n========================================")
    print("🔎 PUMP HUNTER DIAGNOSTICS")
    print("========================================")
    print(f"Tracked Tokens: {total}")
    print(f"In Age Window: {active_window}")
    print(f"In Age + MC Window: {mc_window}")
    print(f"In Age + MC + Buy Window: {buy_window}")
    print(f"Full Candidates: {full_candidates}")
    print("")
    print("Example Tokens:")
    if examples:
        for example in examples:
            print(f"- {example}")
    else:
        print("- No active examples right now")
    print("========================================\n")


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


def paper_buy(token):
    mint = token["mint"]

    if mint in open_trades or mint in already_traded:
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
        "current_pnl": 0,
        "status": "OPEN",
        "buys_at_entry": token["buys"],
        "sells_at_entry": token["sells"],
    }

    open_trades[mint] = trade
    already_traded.add(mint)

    trade_history.setdefault("opened", []).append(trade.copy())
    save_history()

    print("\n########################################")
    print("🟢 PAPER BUY OPENED")
    print("########################################")
    print(f"Token: {token['name']} ({token['symbol']})")
    print(f"Age: {trade['entry_age']}s")
    print(f"Entry Market Cap: {money(token['market_cap'])}")
    print(f"Buys/Sells at Entry: {token['buys']} / {token['sells']}")
    print(f"Paper Size: {money(PAPER_BUY_USD)}")
    print("Rule: sell 50% at +100%, moonbag rides")
    print("########################################\n")

    print_account_summary(force=True)


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

    print("\n########################################")
    print("🔴 PAPER TRADE CLOSED")
    print("########################################")
    print(f"Token: {trade['name']} ({trade['symbol']})")
    print(f"Reason: {reason}")
    print(f"P&L: {pct(pnl)}")
    print("########################################\n")

    print_account_summary(force=True)


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

    trade["current_pnl"] = round(pnl, 2)
    trade["highest_pnl"] = max(trade.get("highest_pnl", 0), pnl)
    trade["lowest_pnl"] = min(trade.get("lowest_pnl", 0), pnl)

    if pnl <= STOP_LOSS:
        close_trade(mint, "STOP LOSS", pnl)
        return

    if pnl >= TAKE_INITIAL_AT and not trade.get("initial_taken"):
        trade["initial_taken"] = True
        trade["position_percent"] = 50

        print("\n########################################")
        print("✅ INITIAL CAPITAL RECOVERED")
        print("########################################")
        print(f"Token: {trade['name']} ({trade['symbol']})")
        print(f"P&L: {pct(pnl)}")
        print("Sold 50% paper position. Remaining 50% is moonbag.")
        print("########################################\n")

        print_account_summary(force=True)

    if trade.get("initial_taken"):
        trailing_stop = trade["highest_pnl"] - MOONBAG_TRAIL
        if pnl <= trailing_stop:
            close_trade(mint, "TRAILING STOP AFTER MOONBAG", pnl)


def qualifies_for_entry(token):
    age = now() - token["created_at"]
    market_cap = token.get("market_cap", 0)
    buys = token.get("buys", 0)
    sells = token.get("sells", 0)

    if token["mint"] in already_traded:
        return False

    if token["mint"] in open_trades:
        return False

    if len(open_trades) >= MAX_OPEN_TRADES:
        return False

    if age < MIN_ENTRY_AGE or age > MAX_ENTRY_AGE:
        return False

    if market_cap < MIN_ENTRY_MARKET_CAP:
        return False

    if market_cap > MAX_ENTRY_MARKET_CAP:
        return False

    if buys < MIN_BUYS_AT_ENTRY:
        return False

    if buys <= sells:
        return False

    print(
        f"✅ CANDIDATE {token['symbol']} | "
        f"Age {age}s | "
        f"MC {money(market_cap)} | "
        f"B/S {buys}/{sells}"
    )

    return True


async def evaluator_loop():
    while True:
        await asyncio.sleep(EVALUATE_EVERY_SECONDS)

        for token in list(tokens.values()):
            update_open_trade(token)

            if qualifies_for_entry(token):
                paper_buy(token)

        print_account_summary(force=False)
        print_diagnostics(force=False)


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
    print("🚀 PUMP HUNTER v8 DIAGNOSTIC STARTED")
    print("========================================")
    print("Paper trading only. No real buying.")
    print("")
    print("Entry Rules:")
    print(f"- Age: {MIN_ENTRY_AGE}s to {MAX_ENTRY_AGE}s")
    print(f"- Market Cap: {money(MIN_ENTRY_MARKET_CAP)} to {money(MAX_ENTRY_MARKET_CAP)}")
    print(f"- Minimum Buys: {MIN_BUYS_AT_ENTRY}")
    print("- Must have more buys than sells")
    print(f"- Max Open Trades: {MAX_OPEN_TRADES}")
    print("")
    print("Diagnostics:")
    print(f"- Prints every {DIAGNOSTIC_EVERY_SECONDS}s")
    print("- Shows why trades are not triggering")
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
                print("Listening silently. Will print summaries, diagnostics and trades.\n")

                async for message in ws:
                    await handle_message(message, ws)

        except Exception as e:
            print(f"Connection error: {e}")
            print("Reconnecting in 10 seconds...\n")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
