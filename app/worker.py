import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from backend.portfolio_engine import PortfolioEngine
from backend.signal_engine import SignalEngine


async def main() -> None:
    signal_engine = SignalEngine()
    portfolio_engine = PortfolioEngine()
    pairs = ["EURUSD", "GBPUSD", "USDJPY"]

    signals = await signal_engine.generate_live_signals(pairs)
    Path("signals_data").mkdir(exist_ok=True)

    Path("signals_data/signals.json").write_text(
        json.dumps(
            {
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                "signals": signals,
                "portfolio": portfolio_engine.rank_signals(signals),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    Path("signals_data/market_ideas.json").write_text(json.dumps(portfolio_engine.market_ideas(), ensure_ascii=False, indent=2), encoding="utf-8")
    Path("signals_data/market_news.json").write_text(json.dumps(portfolio_engine.market_news(), ensure_ascii=False, indent=2), encoding="utf-8")
    Path("signals_data/calendar.json").write_text(json.dumps(portfolio_engine.calendar_events(), ensure_ascii=False, indent=2), encoding="utf-8")
    Path("signals_data/heatmap.json").write_text(json.dumps(portfolio_engine.heatmap(signals), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
