from __future__ import annotations

from app.schemas.analytics import FeatureExtractionResult, FeatureValue, NormalizedAnalyticsBundle


class AnalyticsFeatureExtractor:
    def extract(self, bundle: NormalizedAnalyticsBundle, *, news_score: float, macro_score: float) -> FeatureExtractionResult:
        spread_value, spread_status = self._spread(bundle)
        imbalance_value, imbalance_status = self._imbalance(bundle)
        delta_value, delta_status = self._delta(bundle)
        cumulative_delta_value, cumulative_status = self._cumulative_delta(bundle)
        basis_value, basis_status = self._basis(bundle)
        oi_change_value, oi_change_status = self._oi_change(bundle)
        put_call_oi_value, put_call_oi_status = self._put_call_ratio(bundle, field="open_interest")
        put_call_volume_value, put_call_volume_status = self._put_call_ratio(bundle, field="volume")
        iv_skew_value, iv_skew_status = self._iv_skew(bundle)

        return FeatureExtractionResult(
            spread=FeatureValue(
                name="spread",
                value=spread_value,
                unit="price",
                status=spread_status,
                description_ru="Разница между лучшим ask и лучшим bid.",
            ),
            order_book_imbalance=FeatureValue(
                name="order_book_imbalance",
                value=imbalance_value,
                unit="ratio",
                status=imbalance_status,
                description_ru="Дисбаланс стакана по top-of-book и ближайшим уровням.",
            ),
            delta=FeatureValue(
                name="delta",
                value=delta_value,
                unit="signed_volume",
                status=delta_status,
                description_ru="Разница агрессивных buy/sell объёмов по tick data.",
            ),
            cumulative_delta=FeatureValue(
                name="cumulative_delta",
                value=cumulative_delta_value,
                unit="signed_volume",
                status=cumulative_status,
                description_ru="Накопленная дельта по последовательности тиков.",
            ),
            futures_spot_basis=FeatureValue(
                name="futures_spot_basis",
                value=basis_value,
                unit="price",
                status=basis_status,
                description_ru="Разница цены фьючерса и spot mid-price.",
            ),
            oi_change=FeatureValue(
                name="oi_change",
                value=oi_change_value,
                unit="contracts",
                status=oi_change_status,
                description_ru="Изменение open interest относительно предыдущего значения.",
            ),
            put_call_oi_ratio=FeatureValue(
                name="put_call_oi_ratio",
                value=put_call_oi_value,
                unit="ratio",
                status=put_call_oi_status,
                description_ru="Отношение суммарного put OI к call OI.",
            ),
            put_call_volume_ratio=FeatureValue(
                name="put_call_volume_ratio",
                value=put_call_volume_value,
                unit="ratio",
                status=put_call_volume_status,
                description_ru="Отношение put volume к call volume.",
            ),
            iv_skew=FeatureValue(
                name="iv_skew",
                value=iv_skew_value,
                unit="vol_points",
                status=iv_skew_status,
                description_ru="Смещение implied volatility между put и call опционной кривой.",
            ),
            news_impact_score=FeatureValue(
                name="news_impact_score",
                value=news_score,
                unit="score",
                status="computed" if bundle.news_feed else "partial",
                description_ru="Агрегированный impact score новостного потока.",
            ),
            macro_event_impact_score=FeatureValue(
                name="macro_event_impact_score",
                value=macro_score,
                unit="score",
                status="computed" if bundle.economic_calendar else "partial",
                description_ru="Агрегированный impact score экономического календаря.",
            ),
        )

    @staticmethod
    def _spread(bundle: NormalizedAnalyticsBundle) -> tuple[float | None, str]:
        if bundle.quote is None:
            return None, "unavailable"
        return round(bundle.quote.ask_price - bundle.quote.bid_price, 6), "computed"

    @staticmethod
    def _imbalance(bundle: NormalizedAnalyticsBundle) -> tuple[float | None, str]:
        if bundle.quote is None:
            return None, "unavailable"
        bid_total = sum(level.size for level in bundle.quote.bid_book) or bundle.quote.bid_size
        ask_total = sum(level.size for level in bundle.quote.ask_book) or bundle.quote.ask_size
        denominator = bid_total + ask_total
        if denominator <= 0:
            return None, "partial"
        return round((bid_total - ask_total) / denominator, 4), "computed"

    @staticmethod
    def _delta(bundle: NormalizedAnalyticsBundle) -> tuple[float | None, str]:
        if not bundle.ticks:
            return None, "unavailable"
        value = 0.0
        for tick in bundle.ticks:
            if tick.side == "buy":
                value += tick.size
            elif tick.side == "sell":
                value -= tick.size
        return round(value, 4), "computed"

    def _cumulative_delta(self, bundle: NormalizedAnalyticsBundle) -> tuple[float | None, str]:
        return self._delta(bundle)

    @staticmethod
    def _basis(bundle: NormalizedAnalyticsBundle) -> tuple[float | None, str]:
        if bundle.quote is None or bundle.futures is None:
            return None, "unavailable"
        return round(bundle.futures.last_price - bundle.quote.mid_price, 6), "computed"

    @staticmethod
    def _oi_change(bundle: NormalizedAnalyticsBundle) -> tuple[float | None, str]:
        if bundle.open_interest is None or bundle.open_interest.previous_open_interest is None:
            return None, "unavailable"
        return round(bundle.open_interest.open_interest - bundle.open_interest.previous_open_interest, 2), "computed"

    @staticmethod
    def _put_call_ratio(bundle: NormalizedAnalyticsBundle, *, field: str) -> tuple[float | None, str]:
        if not bundle.options_chain:
            return None, "unavailable"
        puts = sum(getattr(option, field) or 0.0 for option in bundle.options_chain if option.option_type == "put")
        calls = sum(getattr(option, field) or 0.0 for option in bundle.options_chain if option.option_type == "call")
        if calls <= 0:
            return None, "partial"
        return round(puts / calls, 4), "computed"

    @staticmethod
    def _iv_skew(bundle: NormalizedAnalyticsBundle) -> tuple[float | None, str]:
        if not bundle.options_chain:
            return None, "unavailable"
        put_ivs = [option.implied_volatility for option in bundle.options_chain if option.option_type == "put" and option.implied_volatility is not None]
        call_ivs = [option.implied_volatility for option in bundle.options_chain if option.option_type == "call" and option.implied_volatility is not None]
        if not put_ivs or not call_ivs:
            return None, "partial"
        return round((sum(put_ivs) / len(put_ivs)) - (sum(call_ivs) / len(call_ivs)), 4), "computed"
