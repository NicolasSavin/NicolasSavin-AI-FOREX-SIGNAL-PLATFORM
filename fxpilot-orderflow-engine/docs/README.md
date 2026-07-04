# FXPilot Orderflow Engine

Модульная заготовка orderflow-движка для будущей интеграции реальных Databento данных.

## Структура

- `app/` — сервисный слой и модели контрактов.
- `providers/databento/` — adapter для Databento без synthetic fallback.
- `calculators/` — расчёт bias/confidence по реальной дельте и imbalance.
- `api/` — FastAPI router `/api/orderflow/{symbol}`.
- `config/` — настройки окружения.
- `tests/` — unit-тесты контракта недоступных и реальных данных.
- `docker/` — контейнерная заготовка.

## Принцип данных

Если Databento не настроен или live SDK не подключён, engine возвращает `data_status=unavailable`, `side=neutral` и русское предупреждение. Orderflow-метрики не подделываются.
