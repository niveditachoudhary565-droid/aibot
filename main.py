import os
import threading
import time
from datetime import datetime

import MetaTrader5 as mt5
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from config import SYMBOLS
from data_engine import fetch_multi_timeframe_data
from ai_brain import get_ai_decision
from execution import execute_trade
from position_manager import manage_trailing_stops
from ai_brain import STRATEGY_RULES
from memory_store import append_trade_memory, load_trade_memory, reconcile_closed_trades
from auditor import load_rules, run_audit

MIN_CONFIDENCE = 65

app = FastAPI()


state = {
    "running": False,
    "interval": 30,
    "equity": 0,
    "symbol": "-",
    "signal": "-",
    "confidence": 0,
    "logic": "Engine not started",
    "risk_percent": 1.0,
    "stop_loss": None,
    "take_profit": None,
    "atr_14": None,
    "entry_price": None,
    "trade_history": load_trade_memory()[-50:],
    "rule_count": len(load_rules().get("rules", [])),
    "trailing_updates": [],
}


def has_open_position(symbol):
    pos = mt5.positions_get(symbol=symbol)
    return pos is not None and len(pos) > 0


def run_cycle():
    for symbol in SYMBOLS:
        if not state["running"]:
            return
        try:
            data = fetch_multi_timeframe_data(symbol)
            state["trailing_updates"] = manage_trailing_stops(symbol)
            state["equity"] = data["equity"]
            decision = get_ai_decision(data, symbol)
            signal = str(decision.get("signal", "HOLD")).upper()
            try:
                confidence = float(decision.get("confidence_score", 0))
            except Exception:
                confidence = 0
            state.update(
                symbol=symbol,
                signal=signal,
                confidence=confidence,
                logic=decision.get("logic", ""),
                stop_loss=decision.get("stop_loss"),
                take_profit=decision.get("take_profit"),
                atr_14=decision.get("atr_14"),
                entry_price=decision.get("entry_price"),
            )
            if (
                signal in ("BUY", "SELL")
                and confidence >= MIN_CONFIDENCE
                and not has_open_position(symbol)
            ):
                result = execute_trade(
                    symbol, signal,
                    stop_loss=decision.get("stop_loss"),
                    take_profit=decision.get("take_profit"),
                    risk_percent=state["risk_percent"],
                )
                trade = {
                    "id": int(time.time() * 1000),
                    "time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    "symbol": symbol,
                    "side": signal,
                    "fill_price": result.get("price"),
                    "stop_loss": result.get("sl", decision.get("stop_loss")),
                    "take_profit": result.get("tp", decision.get("take_profit")),
                    "volume": result.get("volume"),
                    "risk_percent": state["risk_percent"],
                    "status": result.get("status"),
                }
                if result.get("status") == "done":
                    context = data.get("market_context", {})
                    memory_record = {
                        "ticket": result.get("ticket"),
                        "deal_id": result.get("deal_id"),
                        "symbol": symbol,
                        "side": signal,
                        "entry_price": result.get("price"),
                        "stop_loss": result.get("sl"),
                        "take_profit": result.get("tp"),
                        "volume": result.get("volume"),
                        "risk_percent": state["risk_percent"],
                        "confidence": confidence,
                        "ai_logic": decision.get("logic", ""),
                        "opened_at": trade["time"],
                        "status": "CONFIRMED",
                        "market_context": {
                            "h1": {
                                "atr": context.get("h1", {}).get("atr_14"),
                                "relative_volume": context.get("h1", {}).get("relative_volume_20"),
                                "tick_volume": context.get("h1", {}).get("tick_volume"),
                                "rsi": context.get("h1", {}).get("rsi_14"),
                                "price_vs_ema200": context.get("h1", {}).get("price_vs_ema_200_percent"),
                            },
                            "daily": {
                                "atr": context.get("daily", {}).get("atr_14"),
                                "relative_volume": context.get("daily", {}).get("relative_volume_20"),
                                "tick_volume": context.get("daily", {}).get("tick_volume"),
                                "rsi": context.get("daily", {}).get("rsi_14"),
                                "price_vs_ema200": context.get("daily", {}).get("price_vs_ema_200_percent"),
                            },
                            "correlated_asset_prices": data.get("correlated_asset_prices", {}),
                        },
                    }
                    try:
                        append_trade_memory(memory_record)
                    except Exception:
                        pass
                state["trade_history"].append(trade)
                state["trade_history"] = state["trade_history"][-50:]
        except Exception as e:
            state["logic"] = f"{symbol} error: {e}"
    try:
        reconcile_closed_trades()
        state["trade_history"] = load_trade_memory()[-50:]
    except Exception:
        pass


def engine_loop():
    while True:
        if state["running"]:
            try:
                run_cycle()
            except Exception as e:
                state["logic"] = f"Engine error: {e}"
            waited = 0
            while state["running"] and waited < state["interval"]:
                time.sleep(1)
                waited += 1
        else:
            time.sleep(1)


def audit_loop():
    """Run a due audit at startup and then check again every 24 hours."""
    while True:
        try:
            run_audit()
            state["rule_count"] = len(load_rules().get("rules", []))
        except Exception:
            pass
        time.sleep(24 * 60 * 60)


threading.Thread(target=engine_loop, daemon=True).start()
threading.Thread(target=audit_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index():
    path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return "<h2>Dashboard not created yet (Prompt 5)</h2>"


@app.get("/api/status")
def status():
    try:
        state["rule_count"] = len(load_rules().get("rules", []))
    except Exception:
        pass
    return state


@app.get("/api/memory")
def memory():
    try:
        return load_trade_memory()
    except Exception:
        return []


@app.post("/api/audit")
def audit(force: bool = False):
    try:
        summary = run_audit(force=force)
        state["rule_count"] = len(load_rules().get("rules", []))
        return summary
    except Exception:
        return {"status": "error"}


@app.get("/api/rules")
def rules():
    try:
        return load_rules()
    except Exception:
        return {"rules": [], "last_audit_at": None, "trades_analyzed": 0}


@app.get("/api/strategy-rules")
def strategy_rules():
    return {"rules": STRATEGY_RULES, "trailing_activation_atr": 1.0, "trailing_distance_atr": 1.5}


@app.api_route("/api/start", methods=["GET", "POST"])
def start(interval: int = 30, risk_percent: float = 1.0):
    state["interval"] = max(10, interval)
    state["risk_percent"] = min(2.0, max(0.1, risk_percent))
    state["running"] = True
    return {"running": True, "interval": state["interval"], "risk_percent": state["risk_percent"]}


@app.api_route("/api/stop", methods=["GET", "POST"])
def stop():
    state["running"] = False
    return {"running": False}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
