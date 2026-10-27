"""Closed-trade loss auditor that turns sufficiently supported patterns into rules."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from ai_brain import API_URL, MODEL
from config import DEEPSEEK_API_KEY
from memory_store import load_trade_memory, reconcile_closed_trades


RULES_PATH = os.path.join(os.path.dirname(__file__), "new_rules.json")
DEMO_RULES_PATH = os.path.join(os.path.dirname(__file__), "new_rules_demo.json")
EMPTY_RULES = {"rules": [], "last_audit_at": None, "trades_analyzed": 0}
COOLDOWN = timedelta(hours=12)


def load_rules(path=RULES_PATH):
    """Load a valid rule bank, creating a safe empty one when it is absent or invalid."""
    try:
        with open(path, encoding="utf-8") as rules_file:
            data = json.load(rules_file)
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            raise ValueError("invalid rule bank")
        data.setdefault("last_audit_at", None)
        data.setdefault("trades_analyzed", 0)
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        data = dict(EMPTY_RULES)
        if path == RULES_PATH:
            save_rules(data, path)
        return data


def save_rules(data, path=RULES_PATH):
    with open(path, "w", encoding="utf-8") as rules_file:
        json.dump(data, rules_file, indent=2)


def _loss_summary(records):
    summary = []
    for trade in records:
        context = trade.get("market_context", {})
        summary.append({
            "symbol": trade.get("symbol"), "side": trade.get("side"),
            "entry": trade.get("entry_price"), "stop_loss": trade.get("stop_loss"),
            "take_profit": trade.get("take_profit"),
            "realized_profit": trade.get("realized_profit"),
            "h1": {key: context.get("h1", {}).get(key) for key in ("relative_volume", "atr")},
            "daily": {key: context.get("daily", {}).get(key) for key in ("relative_volume", "atr")},
            "correlated_asset_prices": context.get("correlated_asset_prices", {}),
        })
    return summary


def _request_rules(losses):
    prompt = (
        "You are the Chief Risk Officer and quantitative auditor for an automated trading "
        "desk. Analyze these losing trades for recurring loss patterns including low-volume "
        "breakouts, overextended ATR or volatility traps, and adverse cross-asset correlations. "
        "Return raw JSON only: {\"rules\":[{\"affected_symbol\":\"EURUSD or ALL\","
        "\"setup\":\"specific condition\",\"confidence_reduction_points\":15,"
        "\"sample_size\":3,\"evidence\":\"concise evidence\"}]}. "
        "Only propose rules supported by at least three examples. Losing trades: "
        f"{json.dumps(losses, separators=(',', ':'))}"
    )
    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
        timeout=60,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content.replace("```json", "").replace("```", "").strip())
    return parsed.get("rules", []) if isinstance(parsed, dict) else parsed


def _valid_rules(rules):
    valid = []
    for rule in rules if isinstance(rules, list) else []:
        try:
            penalty = int(rule["confidence_reduction_points"])
            sample_size = int(rule["sample_size"])
            symbol = str(rule["affected_symbol"]).upper()
            setup = str(rule["setup"]).strip()
            evidence = str(rule["evidence"]).strip()
            if symbol and setup and evidence and sample_size >= 3 and 15 <= penalty <= 30:
                valid.append({"affected_symbol": symbol, "setup": setup,
                              "confidence_reduction_points": penalty, "sample_size": sample_size,
                              "evidence": evidence})
        except (KeyError, TypeError, ValueError):
            continue
    return valid


def _merge_rules(existing, incoming):
    known = {(rule.get("affected_symbol"), rule.get("setup")) for rule in existing}
    additions = [rule for rule in incoming if (rule["affected_symbol"], rule["setup"]) not in known]
    return existing + additions, len(additions)


def run_audit(force=False):
    """Reconcile and audit recent closed trades; errors return a safe status summary."""
    try:
        reconcile_closed_trades()
        bank = load_rules()
        closed = [trade for trade in load_trade_memory() if trade.get("status") == "CLOSED" and "realized_profit" in trade][-50:]
        if len(closed) < 3:
            return {"status": "insufficient_trades", "trades_analyzed": len(closed)}
        last_audit = bank.get("last_audit_at")
        if not force and last_audit:
            try:
                if datetime.now(timezone.utc) - datetime.fromisoformat(last_audit.replace("Z", "+00:00")) < COOLDOWN:
                    return {"status": "cooldown", "trades_analyzed": len(closed)}
            except (TypeError, ValueError):
                pass
        losses = [trade for trade in closed if float(trade.get("realized_profit", 0)) < 0]
        if not losses:
            return {"status": "no_losses", "trades_analyzed": len(closed)}
        proposed = _valid_rules(_request_rules(_loss_summary(losses)))
        bank["rules"], added = _merge_rules(bank["rules"], proposed)
        bank["last_audit_at"] = datetime.now(timezone.utc).isoformat()
        bank["trades_analyzed"] = len(closed)
        save_rules(bank)
        return {"status": "completed", "trades_analyzed": len(closed), "losses_analyzed": len(losses), "rules_added": added}
    except Exception as error:
        return {"status": "error", "message": str(error)}


def run_demo():
    """Write a deterministic demo audit without reading live memory or live rule storage."""
    losses = [{"symbol": "EURUSD", "side": "BUY", "entry_price": 1.1, "stop_loss": 1.0985,
               "take_profit": 1.103, "realized_profit": -100, "market_context": {}} for _ in range(3)]
    demo_rule = {"affected_symbol": "EURUSD", "setup": "Low-volume breakout with elevated H1 ATR",
                 "confidence_reduction_points": 20, "sample_size": 3,
                 "evidence": "All three demonstration losses shared the same low-volume volatility setup."}
    result = {"rules": [demo_rule], "last_audit_at": datetime.now(timezone.utc).isoformat(), "trades_analyzed": len(losses)}
    save_rules(result, DEMO_RULES_PATH)
    return {"status": "demo_completed", "rules_added": 1, "trades_analyzed": len(losses)}


if __name__ == "__main__":
    if "--demo" in sys.argv:
        print(json.dumps(run_demo(), indent=2))
    else:
        print(json.dumps(run_audit(), indent=2))
