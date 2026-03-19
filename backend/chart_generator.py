import random
from datetime import datetime, timedelta

def generate_fake_candles():
    candles = []
    price = 1.10

    for i in range(120):
        open_price = price
        close = price + random.uniform(-0.003, 0.003)
        high = max(open_price, close) + random.uniform(0, 0.002)
        low = min(open_price, close) - random.uniform(0, 0.002)

        candles.append({
            "time": (datetime.utcnow() - timedelta(minutes=60 * (120-i))).isoformat(),
            "open": round(open_price, 5),
            "high": round(high, 5),
            "low": round(low, 5),
            "close": round(close, 5),
        })

        price = close

    return candles


class ChartGenerator:

    def generate_chart(self, instrument, idea):

        candles = generate_fake_candles()

        return {
            "candles": candles,

            "zones": [
                {
                    "type": "bullish_ob",
                    "startIndex": 20,
                    "endIndex": 50,
                    "from": min(c["low"] for c in candles[20:50]),
                    "to": max(c["high"] for c in candles[20:50]),
                },
                {
                    "type": "fvg",
                    "startIndex": 60,
                    "endIndex": 85,
                    "from": min(c["low"] for c in candles[60:85]),
                    "to": max(c["high"] for c in candles[60:85]),
                }
            ],

            "levels": [
                {"price": candles[-15]["high"]},
                {"price": candles[-15]["low"]}
            ],

            "arrows": [
                {
                    "fromIndex": 40,
                    "toIndex": 110,
                    "fromPrice": candles[40]["close"],
                    "toPrice": candles[110]["close"],
                }
            ]
        }
