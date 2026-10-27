import requests
from config import DEEPSEEK_API_KEY

for model in [ "openrouter/free"]:
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
        json={"model": model,
              "messages": [{"role": "user", "content": "say hi"}]},
    )
    print(model, r.status_code)
    print(r.text[:300])
    print("---")