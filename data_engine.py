import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone
from config import SYMBOLS

FIBONACCI_RATIOS = (0.382, 0.5, 0.618)

def _to_csv(rates):
    lines = ["time,open,high,low,close,tick_volume"]
    for r in rates:
        t = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        lines.append(f'{t},{r["open"]},{r["high"]},{r["low"]},{r["close"]},{r["tick_volume"]}')
    return "\n".join(lines)


def _market_context(rates):
    """Return the latest technical context calculated from chronological bars."""
    frame = pd.DataFrame(rates).sort_values("time").reset_index(drop=True)

    # Wilder-style ATR: true range exponentially smoothed over 14 periods.
    previous_close = frame["close"].shift(1)
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous_close).abs(),
        (frame["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    frame["atr_14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    # RSI based on exponentially smoothed gains and losses (Wilder's method).
    price_change = frame["close"].diff()
    average_gain = price_change.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    average_loss = (-price_change.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    relative_strength = average_gain / average_loss
    frame["rsi_14"] = 100 - (100 / (1 + relative_strength))

    frame["ema_200"] = frame["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    frame["relative_volume_20"] = frame["tick_volume"] / frame["tick_volume"].rolling(20).mean()

    latest = frame.iloc[-1]
    close = float(latest["close"])
    atr = float(latest["atr_14"])
    ema_200 = float(latest["ema_200"])
    relative_volume = float(latest["relative_volume_20"])
    return {
        "close": round(close, 6),
        "atr_14": round(atr, 6),
        "atr_percent": round((atr / close) * 100, 3),
        "rsi_14": round(float(latest["rsi_14"]), 2),
        "ema_200": round(ema_200, 6),
        "price_vs_ema_200_percent": round(((close - ema_200) / ema_200) * 100, 3),
        "relative_volume_20": round(relative_volume, 2),
        "tick_volume": int(latest["tick_volume"]),
        "trend": "BULLISH" if close > ema_200 else "BEARISH",
        "volume_state": "HIGH" if relative_volume >= 1.2 else "LOW" if relative_volume <= 0.8 else "NORMAL",
    }


def _price_action_context(rates):
    """Describe the latest completed H1 price action and a recent Fibonacci range."""
    frame = pd.DataFrame(rates).sort_values("time").reset_index(drop=True)
    if len(frame) < 3:
        return {}
    previous, current = frame.iloc[-2], frame.iloc[-1]
    body = abs(float(current["close"] - current["open"]))
    candle_range = max(float(current["high"] - current["low"]), 1e-12)
    upper_wick = float(current["high"] - max(current["open"], current["close"]))
    lower_wick = float(min(current["open"], current["close"]) - current["low"])
    bullish_engulfing = (current["close"] > current["open"] and previous["close"] < previous["open"] and current["close"] >= previous["open"] and current["open"] <= previous["close"])
    bearish_engulfing = (current["close"] < current["open"] and previous["close"] > previous["open"] and current["close"] <= previous["open"] and current["open"] >= previous["close"])
    if bullish_engulfing:
        pattern, bias = "BULLISH_ENGULFING", "BULLISH"
    elif bearish_engulfing:
        pattern, bias = "BEARISH_ENGULFING", "BEARISH"
    elif lower_wick >= body * 2 and lower_wick / candle_range >= 0.45:
        pattern, bias = "BULLISH_PIN_BAR", "BULLISH"
    elif upper_wick >= body * 2 and upper_wick / candle_range >= 0.45:
        pattern, bias = "BEARISH_PIN_BAR", "BEARISH"
    elif body / candle_range <= 0.1:
        pattern, bias = "DOJI", "NEUTRAL"
    else:
        pattern, bias = "NONE", "NEUTRAL"
    window = frame.tail(55)
    swing_high, swing_low = float(window["high"].max()), float(window["low"].min())
    price_range = swing_high - swing_low
    fib = {str(ratio): round(swing_high - price_range * ratio, 6) for ratio in FIBONACCI_RATIOS}
    close = float(current["close"])
    nearest_ratio, nearest_level = min(fib.items(), key=lambda item: abs(close - item[1]))
    return {"pattern": pattern, "bias": bias, "recent_swing_high": round(swing_high, 6), "recent_swing_low": round(swing_low, 6), "fibonacci_retracements": fib, "nearest_fibonacci_ratio": nearest_ratio, "nearest_fibonacci_level": nearest_level, "fibonacci_distance_percent": round(abs(close - nearest_level) / close * 100, 3) if close else None}
def fetch_multi_timeframe_data(symbol):
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    mt5.symbol_select(symbol, True)
    # EMA-200 needs at least 200 completed bars; extra history warms up indicators.
    daily = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_D1, 0, 250)
    h1 = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_H1, 0, 250)
    if daily is None or h1 is None:
        raise RuntimeError(f"No data for {symbol}: {mt5.last_error()}")
    tick = mt5.symbol_info_tick(symbol)
    symbol_info = mt5.symbol_info(symbol)
    correlated_assets = {}
    for asset in SYMBOLS:
        if asset == symbol:
            continue
        mt5.symbol_select(asset, True)
        asset_tick = mt5.symbol_info_tick(asset)
        if asset_tick is not None:
            correlated_assets[asset] = {
                "bid": asset_tick.bid,
                "ask": asset_tick.ask,
                "mid": round((asset_tick.bid + asset_tick.ask) / 2, 6),
            }
    daily_context = _market_context(daily)
    h1_context = _market_context(h1)
    price_action = _price_action_context(h1)
    return {
        "daily_csv": _to_csv(daily),
        "h1_csv": _to_csv(h1),
        "daily": {
            "close": daily_context["close"],
            "ema200": daily_context["ema_200"],
            "rsi": daily_context["rsi_14"],
            "atr": daily_context["atr_14"],
            "rel_volume": daily_context["relative_volume_20"],
        },
        "h1": {
            "close": h1_context["close"],
            "ema200": h1_context["ema_200"],
            "rsi": h1_context["rsi_14"],
            "atr": h1_context["atr_14"],
            "rel_volume": h1_context["relative_volume_20"],
        },
        "market_context": {
            "daily": daily_context,
            "h1": h1_context,
            "price_action": price_action,
        },
        "equity": mt5.account_info().equity,
        "ask": tick.ask,
        "bid": tick.bid,
        "digits": symbol_info.digits if symbol_info else 5,
        "correlated_asset_prices": correlated_assets,
        "snapshot_time": datetime.now(timezone.utc).isoformat(),
    }
