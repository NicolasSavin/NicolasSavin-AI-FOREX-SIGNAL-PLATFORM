from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.portfolio_engine import PortfolioEngine
from backend.market_data import MarketDataService

app = FastAPI(title="AI Forex Signal Platform")

portfolio_engine = PortfolioEngine()
market_data_service = MarketDataService()

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def home():
    return FileResponse("app/static/index.html")


@app.get("/ideas")
async def ideas_page():
    return FileResponse("app/static/ideas.html")


@app.get("/ideas/market")
async def market_ideas():
    return portfolio_engine.market_ideas()


@app.get("/api/chart/{symbol}/{timeframe}")
async def chart_data(symbol: str, timeframe: str):
    candles = market_data_service.get_candles(symbol=symbol.upper(), timeframe=timeframe.upper())
    overlays = portfolio_engine.get_chart_overlays(symbol=symbol.upper(), timeframe=timeframe.upper())

    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe.upper(),
        "candles": candles,
        "overlays": overlays,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/health")
async def api_health():
    return {"status": "ok"}
