from backend.snapshot_service import take_tv_snapshot


@app.get("/api/snapshot/{symbol}/{tf}")
async def snapshot(symbol: str, tf: str):
    url = await take_tv_snapshot(symbol, tf)
    return {"image_url": url}
