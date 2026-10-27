import json
import os
import requests
from config import DEEPSEEK_API_KEY

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "openrouter/free"
ATR_STOP_MULTIPLIER = 1.5
ATR_TARGET_MULTIPLIER = 3.0
RULES_PATH = os.path.join(os.path.dirname(__file__), "new_rules.json")

STRATEGY_RULES = [
    "Only enter when Daily trend and H1 momentum are aligned.",
    "Price-action confirmation requires a matching engulfing or pin-bar candle; a doji is no confirmation.",
    "Prefer entries within 0.25% of the H1 38.2%, 50%, or 61.8% Fibonacci retracement of the recent 55-bar swing.",
    "After a position gains 1 H1 ATR, trail its stop by 1.5 H1 ATR; never loosen a stop.",
]


def _active_risk_rules(symbol):
    """Return symbol-specific and desk-wide rules without disrupting AI decisions."""
    try:
        with open(RULES_PATH, encoding="utf-8") as rules_file:
            rules = json.load(rules_file).get("rules", [])
        return [rule for rule in rules if rule.get("affected_symbol") in (symbol, "ALL")]
    except (OSError, ValueError, AttributeError):
        return []


def _validate_or_add_atr_exits(decision, market_data):
    """Keep valid AI exits, otherwise derive directional levels from H1 ATR."""
    signal = str(decision.get("signal", "HOLD")).upper()
    decision["signal"] = signal
    if signal not in ("BUY", "SELL"):
        return decision

    try:
        entry = float(market_data["ask"] if signal == "BUY" else market_data.get("bid", market_data["ask"]))
        atr = float(market_data["market_context"]["h1"]["atr_14"])
        digits = int(market_data.get("digits", 5))
        if atr <= 0:
            raise ValueError("ATR must be positive")
        try:
            stop_loss = float(decision["stop_loss"])
            take_profit = float(decision["take_profit"])
            valid_exits = (
                (signal == "BUY" and stop_loss < entry < take_profit)
                or (signal == "SELL" and take_profit < entry < stop_loss)
            )
        except (KeyError, TypeError, ValueError):
            valid_exits = False

        if not valid_exits:
            stop_distance = atr * ATR_STOP_MULTIPLIER
            target_distance = atr * ATR_TARGET_MULTIPLIER
            stop_loss = entry - stop_distance if signal == "BUY" else entry + stop_distance
            take_profit = entry + target_distance if signal == "BUY" else entry - target_distance

        decision.update({
            "entry_price": round(entry, digits),
            "stop_loss": round(stop_loss, digits),
            "take_profit": round(take_profit, digits),
            "atr_14": round(atr, digits),
        })
    except (KeyError, TypeError, ValueError) as error:
        decision["exit_error"] = f"ATR exit calculation unavailable: {error}"
    return decision

def get_ai_decision(market_data, symbol):
    market_context = json.dumps(market_data.get("market_context", {}), separators=(",", ":"))
    indicators = json.dumps({
        "daily": market_data.get("daily", {}),
        "h1": market_data.get("h1", {}),
    }, separators=(",", ":"))
    active_rules = json.dumps(_active_risk_rules(symbol), separators=(",", ":"))
    system_prompt = (
        f"You are an elite quant trader analyzing {symbol}. "
        "Analyze price action and market structure using the Daily data "
        "(macro trend) and H1 data (micro momentum). "
        "Use the supplied Daily and H1 indicators as follows: EMA200 defines the "
        "long-term trend (price above it is bullish; below it is bearish); RSI(14) "
        "flags overbought or oversold momentum; relative volume (current tick volume "
        "divided by its 20-bar average) confirms breakouts when elevated; and ATR(14) "
        "measures volatility and the viability of risk levels. The compact indicator "
        f"values are: {indicators}. Additional market context is: {market_context}. "
        "PRICE ACTION AND FIBONACCI ENTRY RULES: Only treat BULLISH_ENGULFING or BULLISH_PIN_BAR "
        "as BUY confirmation and BEARISH_ENGULFING or BEARISH_PIN_BAR as SELL confirmation. A DOJI "
        "or opposite pattern means HOLD unless other evidence is exceptionally strong. Prefer a setup "
        "whose H1 close is within 0.25 percent of a supplied 38.2, 50, or 61.8 Fibonacci retracement; "
        "otherwise reduce confidence. "
        "ACTIVE RISK AUDIT RULES: "
        f"{active_rules}. If an active rule's setup matches the current market conditions, "
        "lower confidence_score by its confidence_reduction_points and explicitly state the "
        "applied rule and penalty in logic. "
        "Give extra weight to aligned multi-timeframe trend, momentum, volatility, and volume; "
        "prefer HOLD when those signals conflict. "
        "For BUY or SELL, return exact stop_loss and take_profit prices calculated from "
        "the H1 ATR: stop loss about 1.5 ATR from entry and take profit about 3 ATR from "
        "entry. BUY requires stop_loss < entry < take_profit; SELL requires "
        "take_profit < entry < stop_loss. "
        "Respond ONLY with valid JSON, no extra text, in EXACTLY this format: "
        '{"signal": "BUY", "confidence_score": 85, '
        '"stop_loss": 1.2345, "take_profit": 1.2390, '
        '"logic": "2 sentences explaining your reasoning"} '
        "signal must be BUY, SELL or HOLD. confidence_score is 0-100."
    )
    user_prompt = (
        f"Daily candles (CSV):\n{market_data['daily_csv']}\n\n"
        f"H1 candles (CSV):\n{market_data['h1_csv']}\n\n"
        f"Current ask price: {market_data['ask']}"
    )
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(API_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        content = content.replace("```json", "").replace("```", "").strip()
        decision = _validate_or_add_atr_exits(json.loads(content), market_data)
        if float(decision.get("confidence_score", 0)) < 65:
            decision["signal"] = "HOLD"
        return decision
    except Exception as e:
        return {"signal": "HOLD", "confidence_score": 0, "logic": f"AI error: {e}"}
