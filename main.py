from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Forex Signal Platform 3.0"}


@app.get("/signals")
def get_signals():
    return {
        "status": "ok",
        "signals": [
            {
                "pair": "EURUSD",
                "signal": "BUY",
                "entry": 1.0850,
                "tp": 1.0900,
                "sl": 1.0820
            },
            {
                "pair": "GBPUSD",
                "signal": "SELL",
                "entry": 1.2700,
                "tp": 1.2620,
                "sl": 1.2740
            }
        ]
    }
