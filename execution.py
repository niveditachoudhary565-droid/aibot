import MetaTrader5 as mt5

def execute_trade(symbol, signal, stop_loss, take_profit, risk_percent):
    """Submit a risk-sized order using the supplied, validated exit prices."""
    if signal not in ("BUY", "SELL"):
        return {"status": "skipped", "reason": f"signal is {signal}"}

    if not mt5.initialize():
        return {"status": "error", "reason": f"MT5 init failed: {mt5.last_error()}"}

    info = mt5.symbol_info(symbol)
    if info is None:
        return {"status": "error", "reason": f"{symbol} not found"}
    if not info.visible:
        mt5.symbol_select(symbol, True)

    tick = mt5.symbol_info_tick(symbol)
    account = mt5.account_info()
    if tick is None or account is None:
        return {"status": "error", "reason": "tick or account information unavailable"}
    digits = info.digits

    if signal == "BUY":
        price = tick.ask
        sl = round(float(stop_loss), digits)
        tp = round(float(take_profit), digits)
        order_type = mt5.ORDER_TYPE_BUY
    else:
        price = tick.bid
        sl = round(float(stop_loss), digits)
        tp = round(float(take_profit), digits)
        order_type = mt5.ORDER_TYPE_SELL

    if not ((signal == "BUY" and sl < price < tp) or (signal == "SELL" and tp < price < sl)):
        return {"status": "skipped", "reason": "stop loss or take profit is on the wrong side of entry"}

    try:
        target_risk = float(account.equity) * (float(risk_percent) / 100)
        tick_size = float(getattr(info, "trade_tick_size", 0) or info.point)
        tick_value = float(getattr(info, "trade_tick_value", 0))
        stop_distance = abs(price - sl)
        risk_per_lot = (stop_distance / tick_size) * tick_value
        if target_risk <= 0 or tick_size <= 0 or tick_value <= 0 or risk_per_lot <= 0:
            raise ValueError("invalid risk or symbol tick specification")
    except (TypeError, ValueError) as error:
        return {"status": "error", "reason": f"cannot calculate position size: {error}"}

    volume_step = float(info.volume_step)
    raw_volume = target_risk / risk_per_lot
    volume = max(float(info.volume_min), (raw_volume // volume_step) * volume_step)
    volume = min(volume, float(info.volume_max))
    volume = round(volume, max(0, len(str(volume_step).split(".")[-1].rstrip("0"))))
    minimum_risk = float(info.volume_min) * risk_per_lot
    if minimum_risk > target_risk * 2:
        return {"status": "skipped", "reason": "minimum volume exceeds twice the target risk", "price": price, "sl": sl, "tp": tp, "volume": float(info.volume_min)}

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 123456,
        "comment": "ai-bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None:
        return {"status": "error", "reason": str(mt5.last_error())}
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return {"status": "failed", "retcode": result.retcode, "reason": result.comment}
    return {"status": "done", "symbol": symbol, "signal": signal,
            "price": price, "sl": sl, "tp": tp, "volume": volume,
            "ticket": result.order, "deal_id": getattr(result, "deal", None)}
