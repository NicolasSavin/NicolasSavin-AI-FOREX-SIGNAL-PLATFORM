from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "Forex Signal Platform 3.0"}


@app.get("/signals")
def get_signals():
    return {"status": "ok", "signals": []}
