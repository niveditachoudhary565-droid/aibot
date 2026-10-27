
"""Persistent, local trade memory and MT5 close-deal reconciliation."""
import json
import os
import threading
from datetime import datetime, timezone

import MetaTrader5 as mt5


MEMORY_PATH = os.path.join(os.path.dirname(__file__), "memory.json")
_lock = threading.Lock()


def _load_unlocked():
    try:
        with open(MEMORY_PATH, encoding="utf-8") as memory_file:
            records = json.load(memory_file)
        return records if isinstance(records, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save_unlocked(records):
    temporary_path = f"{MEMORY_PATH}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as memory_file:
        json.dump(records, memory_file, indent=2, default=str)
    os.replace(temporary_path, MEMORY_PATH)


def load_trade_memory():
    """Return a snapshot of saved records, creating the empty store when needed."""
    with _lock:
        records = _load_unlocked()
        if not os.path.exists(MEMORY_PATH):
            _save_unlocked(records)
        return records


def append_trade_memory(record):
    """Append one confirmed fill and save formatted JSON to the memory bank."""
    with _lock:
        records = _load_unlocked()
        records.append(record)
        _save_unlocked(records)


def _deal_value(deal, name, default=None):
    return getattr(deal, name, default)


def _find_exit_deal(ticket):
    """Look first by position, then by order/deal ticket for an exit deal."""
    deals = mt5.history_deals_get(position=int(ticket)) or ()
    if not deals:
        deals = mt5.history_deals_get(ticket=int(ticket)) or ()
    exits = [deal for deal in deals if _deal_value(deal, "entry") == mt5.DEAL_ENTRY_OUT]
    return max(exits, key=lambda deal: _deal_value(deal, "time", 0), default=None)


def reconcile_closed_trades():
    """Mark confirmed memory records closed when MT5 reports their exit deal."""
    newly_reconciled = 0
    with _lock:
        records = _load_unlocked()
        for record in records:
            if record.get("status") != "CONFIRMED" or record.get("closed_at"):
                continue
            ticket = record.get("ticket") or record.get("deal_id")
            if ticket is None:
                continue
            try:
                exit_deal = _find_exit_deal(ticket)
            except Exception:
                continue
            if exit_deal is None:
                continue
            profit = float(_deal_value(exit_deal, "profit", 0.0))
            exit_time = datetime.fromtimestamp(
                int(_deal_value(exit_deal, "time", 0)), tz=timezone.utc
            ).isoformat()
            record.update({
                "exit_price": float(_deal_value(exit_deal, "price", 0.0)),
                "closed_at": exit_time,
                "realized_profit": profit,
                "outcome": "WIN" if profit > 0 else "LOSS" if profit < 0 else "BREAKEVEN",
                "status": "CLOSED",
            })
            newly_reconciled += 1
        if newly_reconciled:
            _save_unlocked(records)
    return newly_reconciled
