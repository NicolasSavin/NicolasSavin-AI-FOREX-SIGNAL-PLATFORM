from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app, signal_analytics_service
from backend.feature_builder import FeatureBuilder
from backend.pattern_detector import PatternDetector


class PatternDetectorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.detector = PatternDetector()

    def _candles_from_closes(self, closes: list[float]) -> list[dict]:
        candles: list[dict] = []
        previous = closes[0]
        for close in closes:
            open_price = previous
            high = max(open_price, close) + 0.01
            low = min(open_price, close) - 0.01
            candles.append(
                {
                    "open": round(open_price, 5),
                    "high": round(high, 5),
                    "low": round(low, 5),
                    "close": round(close, 5),
                    "volume": 1000.0,
                }
            )
            previous = close
        return candles

    def test_detector_handles_empty_and_short_data(self) -> None:
        self.assertEqual(self.detector.detect([])["patterns"], [])
        self.assertEqual(self.detector.detect(self._candles_from_closes([1.0] * 8))["patterns"], [])

    def test_detector_returns_empty_for_flat_market(self) -> None:
        result = self.detector.detect(self._candles_from_closes([1.0] * 20))
        self.assertEqual(result["summary"]["patternsDetected"], 0)
        self.assertEqual(result["patterns"], [])

    def test_detector_finds_double_top(self) -> None:
        closes = [1.00, 1.03, 1.08, 1.14, 1.18, 1.12, 1.06, 1.11, 1.17, 1.18, 1.11, 1.03, 0.99, 0.98]
        result = self.detector.detect(self._candles_from_closes(closes))
        types = {pattern["type"] for pattern in result["patterns"]}
        self.assertIn("double_top", types)
        self.assertEqual(result["summary"]["patternBias"], "bearish")

    def test_detector_finds_bull_flag(self) -> None:
        closes = [1.00, 1.03, 1.07, 1.12, 1.18, 1.24, 1.23, 1.225, 1.22, 1.215, 1.21, 1.205, 1.208, 1.211, 1.214, 1.218]
        result = self.detector.detect(self._candles_from_closes(closes))
        types = {pattern["type"] for pattern in result["patterns"]}
        self.assertIn("bull_flag", types)

    def test_feature_builder_exposes_pattern_fields(self) -> None:
        candles = self._candles_from_closes([1.0, 1.03, 1.08, 1.14, 1.18, 1.12, 1.06, 1.11, 1.17, 1.18, 1.11, 1.03, 0.99, 0.98, 0.97, 0.96, 0.95, 0.94, 0.93, 0.92])
        features = FeatureBuilder().build({"data_status": "real", "candles": candles})
        self.assertIn("chart_patterns", features)
        self.assertIn("pattern_summary", features)
        self.assertIn("pattern_score", features)


class AnalyticsApiPatternContractTestCase(unittest.TestCase):
    def test_analytics_endpoint_contains_pattern_contract(self) -> None:
        mocked_signal = {
            "action": "BUY",
            "confidence_percent": 78,
            "chart_patterns": [
                {
                    "id": "pattern-1",
                    "type": "double_bottom",
                    "title_ru": "Двойное дно",
                    "direction": "bullish",
                    "confidence": 0.81,
                    "startIndex": 2,
                    "endIndex": 12,
                    "breakoutIndex": 12,
                    "neckline": 1.105,
                    "supportLevel": 1.08,
                    "resistanceLevel": 1.105,
                    "targetLevel": 1.13,
                    "invalidationLevel": 1.075,
                    "description_ru": "Две впадины усиливают разворот вверх.",
                    "explanation_ru": "Тестовый паттерн для проверки API.",
                    "points": [],
                    "status": "confirmed",
                    "createdAt": "2026-03-18T00:00:00Z",
                }
            ],
            "pattern_summary": {
                "patternsDetected": 1,
                "bullishPatternsCount": 1,
                "bearishPatternsCount": 0,
                "dominantPattern": "double_bottom",
                "dominantPatternTitleRu": "Двойное дно",
                "patternScore": 0.81,
                "patternBias": "bullish",
                "patternSummaryRu": "Найден бычий паттерн.",
            },
            "pattern_signal_impact": {
                "patternAlignmentWithSignal": "supports",
                "patternAlignmentLabelRu": "Паттерны подтверждают направление сигнала",
                "confidenceDelta": 6,
                "conflictingPatternDetected": False,
                "hasBullishPattern": True,
                "hasBearishPattern": False,
                "dominantPatternType": "double_bottom",
                "dominantPatternTitleRu": "Двойное дно",
                "patternConfidence": 0.81,
                "patternScore": 0.81,
                "explanationRu": "Паттерн усиливает лонговый сценарий.",
            },
        }

        with patch.object(signal_analytics_service, '_technical_signal', AsyncMock(return_value=(0.78, 'test-source', mocked_signal))), \
             patch.object(signal_analytics_service.news_connector, 'load', AsyncMock(return_value=([], signal_analytics_service.news_connector._descriptor(status='unavailable', note_ru='test-news-disabled')))):
            client = TestClient(app)
            response = client.get('/api/analytics/signals/EURUSD')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn('chartPatterns', payload)
        self.assertIn('patternSummary', payload)
        self.assertIn('patternSignalImpact', payload)
        self.assertIn('patternFeatures', payload['features'])
        self.assertEqual(payload['patternSummary']['dominantPatternTitleRu'], 'Двойное дно')


if __name__ == '__main__':
    unittest.main()
