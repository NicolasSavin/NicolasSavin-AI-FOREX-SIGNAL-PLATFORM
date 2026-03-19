import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

SNAP_DIR = Path("app/static/chart_images")
SNAP_DIR.mkdir(parents=True, exist_ok=True)


async def take_tv_snapshot(symbol="XAUUSD", timeframe="15"):
    url = f"https://www.tradingview.com/chart/?symbol=OANDA:{symbol}"

    filename = f"{symbol.lower()}_{timeframe}.png"
    path = SNAP_DIR / filename

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto(url)

        # ждём загрузки графика
        await page.wait_for_timeout(5000)

        # делаем скрин
        await page.screenshot(path=str(path))

        await browser.close()

    return f"/static/chart_images/{filename}"
