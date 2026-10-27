from data_engine import fetch_multi_timeframe_data

d = fetch_multi_timeframe_data("EURUSD")
for k, v in d.items():
    print(k, ":", str(v)[:200])