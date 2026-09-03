FXPilot AI Ideas Trader v3.00
=============================

Файл: AI_Ideas_Trader.mq4  (корень репозитория и эта папка)

1. Скопируй AI_Ideas_Trader.mq4 в MQL4/Experts
2. В MetaEditor: Compile
3. MT4 → Сервис → Настройки → Советники
   ☑ Разрешить советникам торговать
   ☑ Разрешить WebRequest для URL:
        https://fxpilot.ru
4. Навесь советник на график (EURUSD / GBPUSD / USDJPY / XAUUSD)
5. Inputs:
   InpApiBaseUrl     = https://fxpilot.ru
   InpUseRiskPercent = true, 0.50% риска на сделку
   InpUseTrailingStop / InpUseBreakEven — включены
   Magic 26042026 — тот же, что в старой версии

Трейлинг и безубыток работают на каждом тике.
Новые входы — раз в InpRefreshSeconds с /api/mt4/signals.
Уровни ENTRY/SL/TP и зона входа — с /api/mt4/markup/{symbol}.

Не путать с FXPilot_MT4_Bridge.mq4: мост шлёт котировки на сайт,
этот советник торгует идеи сайта.
