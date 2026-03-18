from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.portfolio_engine import PortfolioEngine

app = FastAPI(title="AI Forex Signal Platform")
portfolio_engine = PortfolioEngine()

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def home():
    return FileResponse("app/static/ideas.html")


@app.get("/ideas")
async def ideas_page():
    return FileResponse("app/static/ideas.html")


@app.get("/ideas/market")
async def market_ideas():
    return portfolio_engine.market_ideas()


@app.get("/news/market")
async def market_news():
    return portfolio_engine.market_news(active_signals=[])


@app.get("/calendar/events")
async def calendar_events():
    return portfolio_engine.calendar_events()


@app.get("/heatmap")
async def heatmap():
    return portfolio_engine.heatmap([])
