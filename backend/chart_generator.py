import random
from datetime import datetime, timedelta


def generate_fake_candles():
    candles = []
    price = 2000

    for i in range(120):
        open_price = price
        close = price + random.uniform(-5, 5)
        high = max(open_price, close) + random.uniform(0, 3)
        low = min(open_price, close) - random.uniform(0, 3)

        candles.append({
            "time": (datetime.utcnow() - timedelta(hours=(120-i))).isoformat(),
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(close, 2),
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
                {"price": candles[-10]["high"]},
                {"price": candles[-10]["low"]}
            ],

            "arrows": [
                {
                    "fromIndex": 50,
                    "toIndex": 110,
                    "fromPrice": candles[50]["close"],
                    "toPrice": candles[110]["close"],
                }
            ]
        }
