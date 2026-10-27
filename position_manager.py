"""Risk management for already-open MetaTrader 5 positions."""
import MetaTrader5 as mt5

from data_engine import fetch_multi_timeframe_data

TRAIL_ACTIVATION_ATR = 1.0
TRAIL_DISTANCE_ATR = 1.5


def manage_trailing_stops(symbol):
    """Tighten profitable positions only and return the changes made."""
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return []
    try:
        data = fetch_multi_timeframe_data(symbol)
        atr = float(data["market_context"]["h1"]["atr_14"])
        digits = int(data.get("digits", 5))
        tick, info = mt5.symbol_info_tick(symbol), mt5.symbol_info(symbol)
        if not tick or not info or atr <= 0:
            return []
    except Exception:
        return []

    minimum_gap = max(float(getattr(info, "trade_stops_level", 0)) * float(info.point), float(info.point))
    updates = []
    for position in positions:
        is_buy = position.type == mt5.POSITION_TYPE_BUY
        market_price = tick.bid if is_buy else tick.ask
        profit_distance = market_price - position.price_open if is_buy else position.price_open - market_price
        if profit_distance < atr * TRAIL_ACTIVATION_ATR:
            continue
        candidate = round(market_price - atr * TRAIL_DISTANCE_ATR if is_buy else market_price + atr * TRAIL_DISTANCE_ATR, digits)
        current_sl = float(position.sl or 0)
        improves_stop = (is_buy and (current_sl == 0 or candidate > current_sl) and candidate < market_price - minimum_gap) or (not is_buy and (current_sl == 0 or candidate < current_sl) and candidate > market_price + minimum_gap)
        if not improves_stop:
            continue
        result = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "position": position.ticket, "symbol": symbol, "sl": candidate, "tp": position.tp, "magic": 123456, "comment": "ai-bot trailing SL"})
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            updates.append({"ticket": position.ticket, "stop_loss": candidate, "atr_14": round(atr, digits)})
    return updates
