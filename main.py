from fastapi import FastAPI
from pathlib import Path
import json

app = FastAPI()

SIGNALS_FILE = Path("signals_data/signals.json")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Forex Signal Platform 3.0"}

@app.get("/signals")
def get_signals():
    if not SIGNALS_FILE.exists():
        return {"status": "ok", "signals": [], "message": "signals file not found"}

    with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
        signals = json.load(f)

    return {"status": "ok", "signals": signals}

@app.get("/pairs")
def get_pairs():
    if not SIGNALS_FILE.exists():
        return {"status": "ok", "pairs": []}

    with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
        signals = json.load(f)

    pairs = sorted(list({item["pair"] for item in signals if "pair" in item}))
    return {"status": "ok", "pairs": pairs}
