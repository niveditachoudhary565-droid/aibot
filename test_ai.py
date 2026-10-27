from data_engine import fetch_multi_timeframe_data
from ai_brain import get_ai_decision

data = fetch_multi_timeframe_data("EURUSD")
print(get_ai_decision(data, "EURUSD"))